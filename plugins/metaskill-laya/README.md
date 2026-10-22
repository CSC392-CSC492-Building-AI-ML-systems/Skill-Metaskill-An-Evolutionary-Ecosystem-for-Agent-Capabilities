# metaskill-laya

Optional companion to the [metaskill](../../README.md) plugin. Installing it
adds the `laya` package (about 1 GB with its model, downloaded on first use) so
`generalize` can detect personal data with the local Laya Experts model when
called with `use_laya=true`. No API key, no signup.

```bash
hermes plugins install metaskill-laya
```

Hermes asks for consent before installing the dependency. Without this plugin,
metaskill works as before and `use_laya=true` returns an error that points here.
