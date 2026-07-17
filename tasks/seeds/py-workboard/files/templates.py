"""HTML templates as string constants.

No template engine on the provisioning image, so pages are assembled from
string.Template fragments. Handlers escape user data before substitution.
"""
from string import Template

_LAYOUT = Template("""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>$title · workboard</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; color: #222; }
    table { border-collapse: collapse; }
    td, th { padding: .3rem .8rem; border-bottom: 1px solid #ddd; }
    .done { color: #7a7a7a; text-decoration: line-through; }
    nav a { margin-right: 1rem; }
  </style>
</head>
<body>
<nav><a href="/">workboard</a></nav>
<main>
$content
</main>
</body>
</html>
""")

_FRAGMENTS = {
    "dashboard": Template(
        "<h1>Dashboard</h1>\n"
        "<p>$count active project(s)</p>\n"
        "<table>\n<tr><th>Project</th><th>Items</th></tr>\n$rows</table>\n"
    ),
    "dashboard_row": Template(
        '<tr><td><a href="/projects/$id">$name</a></td>'
        "<td>$open open · $done done</td></tr>\n"
    ),
    "dashboard_empty": Template(
        '<tr><td colspan="2">No active projects yet.</td></tr>\n'
    ),
    "project": Template(
        "<h1>$name</h1>\n"
        "<p>$open open · $done done</p>\n"
        "<ul>\n$items</ul>\n"
        '<p><a href="/">back to dashboard</a></p>\n'
    ),
    "project_item": Template('<li class="$status">$title</li>\n'),
    "project_no_items": Template("<li>No items filed.</li>\n"),
}


def render(template_name, **context):
    """Render one named fragment wrapped in the site layout."""
    content = _FRAGMENTS[template_name].substitute(**context)
    return _LAYOUT.substitute(title=context.get("title", "workboard"),
                              content=content)


def fragment(template_name, **context):
    """Render one named fragment on its own (for building up rows/lists)."""
    return _FRAGMENTS[template_name].substitute(**context)
