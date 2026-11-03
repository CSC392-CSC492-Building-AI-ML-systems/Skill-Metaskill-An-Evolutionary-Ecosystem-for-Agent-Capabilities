# metaskill-laya is installed

`generalize` now also finds names, street addresses, birth dates, and ID and card
numbers. Nothing else to configure.

1. **Restart Hermes** if it was running, so it can import the new `laya` package.
2. **Download the model now** (about 840 MB, once), so the first `generalize` does not
   have to:

   ```bash
   hermes metaskill-laya download
   ```

3. Optional, if you have an NVIDIA or AMD GPU with a GPU build of PyTorch: set
   `METASKILL_LAYA_DEVICE=gpu`. On a CPU, expect about 3 seconds per small file.

Check the setup at any time with `hermes metaskill-laya status`. To turn the add-on
off, run `hermes plugins disable metaskill-laya`.
