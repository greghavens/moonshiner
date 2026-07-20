import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVA_ROOT = ROOT / "src" / "main" / "java" / "com" / "acme" / "orders"
RESOURCE_ROOT = ROOT / "src" / "main" / "resources"
WEBAPP_ROOT = ROOT / "src" / "main" / "webapp"
XSI_SCHEMA_LOCATION = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"


def read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def parse(relative_path):
    return ET.parse(ROOT / relative_path).getroot()


def local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def children(element, name):
    return [child for child in element if local_name(child) == name]


def child(element, name):
    matches = children(element, name)
    if len(matches) != 1:
        raise AssertionError(f"expected one {name!r} child, found {len(matches)}")
    return matches[0]


def text_of(element, name):
    value = child(element, name).text
    return value.strip() if value else ""


class JavaNamespaceTests(unittest.TestCase):
    def test_all_component_imports_use_jakarta_packages(self):
        sources = {path.name: path.read_text(encoding="utf-8") for path in JAVA_ROOT.glob("*.java")}
        self.assertTrue(
            {"CorrelationFilter.java", "Order.java", "OrderServlet.java"} <= set(sources)
        )

        for name, source in sources.items():
            self.assertNotRegex(
                source,
                r"\bjavax\.(?:servlet|persistence|validation)\.",
                f"legacy Java EE namespace remains in {name}",
            )

        required_imports = {
            "CorrelationFilter.java": {
                "jakarta.servlet.DispatcherType",
                "jakarta.servlet.Filter",
                "jakarta.servlet.FilterChain",
                "jakarta.servlet.ServletException",
                "jakarta.servlet.ServletRequest",
                "jakarta.servlet.ServletResponse",
                "jakarta.servlet.annotation.WebFilter",
                "jakarta.servlet.http.HttpServletRequest",
                "jakarta.servlet.http.HttpServletResponse",
            },
            "Order.java": {
                "jakarta.persistence.Column",
                "jakarta.persistence.Entity",
                "jakarta.persistence.Id",
                "jakarta.persistence.Table",
                "jakarta.validation.constraints.NotBlank",
                "jakarta.validation.constraints.Size",
            },
            "OrderServlet.java": {
                "jakarta.persistence.EntityManager",
                "jakarta.persistence.PersistenceContext",
                "jakarta.servlet.ServletException",
                "jakarta.servlet.annotation.WebServlet",
                "jakarta.servlet.http.HttpServlet",
                "jakarta.servlet.http.HttpServletRequest",
                "jakarta.servlet.http.HttpServletResponse",
            },
        }
        for name, expected in required_imports.items():
            actual = set(re.findall(r"(?m)^import\s+([\w.]+);$", sources[name]))
            self.assertTrue(expected <= actual, f"missing Jakarta imports in {name}: {sorted(expected - actual)}")

    def test_filter_servlet_validation_and_persistence_contracts_are_preserved(self):
        correlation_filter = read("src/main/java/com/acme/orders/CorrelationFilter.java")
        order = read("src/main/java/com/acme/orders/Order.java")
        servlet = read("src/main/java/com/acme/orders/OrderServlet.java")

        for fragment in (
            "@WebFilter(",
            'filterName = "correlationFilter"',
            'urlPatterns = "/*"',
            "DispatcherType.REQUEST",
            "DispatcherType.ASYNC",
            "asyncSupported = true",
            "public final class CorrelationFilter implements Filter",
            'getHeader("X-Correlation-Id")',
            'setHeader("X-Correlation-Id", correlationId)',
            "chain.doFilter(request, response)",
        ):
            self.assertIn(fragment, correlation_filter)

        for fragment in (
            "@Entity",
            '@Table(name = "purchase_orders")',
            "@Id",
            '@Column(name = "order_id", nullable = false, updatable = false)',
            '@NotBlank(message = "{order.customer.required}")',
            "@Size(min = 2, max = 80)",
            '@Column(name = "customer_name", nullable = false, length = 80)',
            "public Order(long id, String customerName)",
            "public long getId()",
            "public String getCustomerName()",
        ):
            self.assertIn(fragment, order)

        for fragment in (
            '@WebServlet(name = "orderServlet", urlPatterns = "/orders", loadOnStartup = 1)',
            "public final class OrderServlet extends HttpServlet",
            '@PersistenceContext(unitName = "orders")',
            'Long.parseLong(request.getParameter("id"))',
            'request.getParameter("customer")',
            "entityManager.persist(new Order(id, customerName))",
            "HttpServletResponse.SC_CREATED",
        ):
            self.assertIn(fragment, servlet)


class DescriptorNamespaceTests(unittest.TestCase):
    def assert_descriptor_header(self, root, namespace, version, schema_file):
        for element in root.iter():
            self.assertTrue(
                element.tag.startswith("{" + namespace + "}"),
                f"descriptor element is outside {namespace}: {element.tag}",
            )
        self.assertEqual(version, root.attrib.get("version"))
        self.assertEqual(
            f"{namespace} {namespace}/{schema_file}",
            root.attrib.get(XSI_SCHEMA_LOCATION),
        )

    def test_web_descriptor_targets_servlet_6_and_preserves_mappings(self):
        web = parse("src/main/webapp/WEB-INF/web.xml")
        self.assert_descriptor_header(
            web,
            "https://jakarta.ee/xml/ns/jakartaee",
            "6.0",
            "web-app_6_0.xsd",
        )
        self.assertEqual("Orders", text_of(web, "display-name"))

        filter_definition = child(web, "filter")
        self.assertEqual("correlationFilter", text_of(filter_definition, "filter-name"))
        self.assertEqual("com.acme.orders.CorrelationFilter", text_of(filter_definition, "filter-class"))
        self.assertEqual("true", text_of(filter_definition, "async-supported"))

        filter_mapping = child(web, "filter-mapping")
        self.assertEqual("correlationFilter", text_of(filter_mapping, "filter-name"))
        self.assertEqual("/*", text_of(filter_mapping, "url-pattern"))
        self.assertEqual(["REQUEST", "ASYNC"], [node.text.strip() for node in children(filter_mapping, "dispatcher")])

        servlet = child(web, "servlet")
        self.assertEqual("orderServlet", text_of(servlet, "servlet-name"))
        self.assertEqual("com.acme.orders.OrderServlet", text_of(servlet, "servlet-class"))
        self.assertEqual("1", text_of(servlet, "load-on-startup"))
        servlet_mapping = child(web, "servlet-mapping")
        self.assertEqual("orderServlet", text_of(servlet_mapping, "servlet-name"))
        self.assertEqual("/orders", text_of(servlet_mapping, "url-pattern"))

    def test_validation_descriptor_targets_validation_3_and_preserves_settings(self):
        validation = parse("src/main/resources/META-INF/validation.xml")
        self.assert_descriptor_header(
            validation,
            "https://jakarta.ee/xml/ns/validation/configuration",
            "3.0",
            "validation-configuration-3.0.xsd",
        )
        executable = child(validation, "executable-validation")
        self.assertEqual("true", executable.attrib.get("enabled"))
        defaults = child(executable, "default-validated-executable-types")
        self.assertEqual(
            ["CONSTRUCTORS", "NON_GETTER_METHODS"],
            [node.text.strip() for node in children(defaults, "executable-type")],
        )

    def test_persistence_descriptors_target_3_1_and_preserve_mappings(self):
        persistence = parse("src/main/resources/META-INF/persistence.xml")
        self.assert_descriptor_header(
            persistence,
            "https://jakarta.ee/xml/ns/persistence",
            "3.1",
            "persistence_3_1.xsd",
        )
        unit = child(persistence, "persistence-unit")
        self.assertEqual({"name": "orders", "transaction-type": "JTA"}, unit.attrib)
        self.assertEqual("java:comp/DefaultDataSource", text_of(unit, "jta-data-source"))
        self.assertEqual("META-INF/orm.xml", text_of(unit, "mapping-file"))
        self.assertEqual("com.acme.orders.Order", text_of(unit, "class"))
        properties = child(unit, "properties")
        self.assertEqual(
            [{"name": "jakarta.persistence.schema-generation.database.action", "value": "validate"}],
            [node.attrib for node in children(properties, "property")],
        )

        mapping = parse("src/main/resources/META-INF/orm.xml")
        self.assert_descriptor_header(
            mapping,
            "https://jakarta.ee/xml/ns/persistence/orm",
            "3.1",
            "orm_3_1.xsd",
        )
        self.assertEqual("com.acme.orders", text_of(mapping, "package"))
        entity = child(mapping, "entity")
        self.assertEqual({"class": "Order", "access": "FIELD"}, entity.attrib)
        self.assertEqual({"name": "purchase_orders"}, child(entity, "table").attrib)
        attributes = child(entity, "attributes")
        identifier = child(attributes, "id")
        self.assertEqual({"name": "id"}, identifier.attrib)
        self.assertEqual(
            {"name": "order_id", "nullable": "false", "updatable": "false"},
            child(identifier, "column").attrib,
        )
        basic = child(attributes, "basic")
        self.assertEqual({"name": "customerName", "optional": "false"}, basic.attrib)
        self.assertEqual(
            {"name": "customer_name", "nullable": "false", "length": "80"},
            child(basic, "column").attrib,
        )

    def test_deployment_descriptors_are_packaged_at_standard_war_paths(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "orders.war"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for source in sorted(WEBAPP_ROOT.rglob("*")):
                    if source.is_file():
                        archive.write(source, source.relative_to(WEBAPP_ROOT).as_posix())
                for source in sorted(RESOURCE_ROOT.rglob("*")):
                    if source.is_file():
                        archive.write(source, (Path("WEB-INF/classes") / source.relative_to(RESOURCE_ROOT)).as_posix())

            expected_paths = {
                "WEB-INF/web.xml",
                "WEB-INF/classes/META-INF/persistence.xml",
                "WEB-INF/classes/META-INF/orm.xml",
                "WEB-INF/classes/META-INF/validation.xml",
            }
            with zipfile.ZipFile(archive_path) as archive:
                self.assertTrue(expected_paths <= set(archive.namelist()))
                for path in expected_paths:
                    descriptor = archive.read(path).decode("utf-8")
                    self.assertIn("https://jakarta.ee/xml/ns/", descriptor)
                    self.assertNotIn("http://xmlns.jcp.org/xml/ns/", descriptor)


if __name__ == "__main__":
    unittest.main()
