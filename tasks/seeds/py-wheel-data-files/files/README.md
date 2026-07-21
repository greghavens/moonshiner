# Letterpress

Letterpress renders small messages from templates shipped with the Python
package.

Run the source-tree checks with:

```sh
PYTHONPATH=src python -m unittest tests.test_source_tree -v
```

The release acceptance suite is:

```sh
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 \
  python -m unittest discover -s tests -p 'test_*.py' -v
```

The acceptance suite is offline. It builds and installs the wheel in a temporary
directory and also checks an editable install.
