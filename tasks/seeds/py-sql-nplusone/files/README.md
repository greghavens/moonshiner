# Article feed repository

This small project uses only Python's standard library. Run the tests with:

```sh
python -m unittest discover -s tests -v
```

`ArticleRepository.list_feed` returns published articles for one tenant at or
above a requested score. Each result includes its active author when one is
available.
