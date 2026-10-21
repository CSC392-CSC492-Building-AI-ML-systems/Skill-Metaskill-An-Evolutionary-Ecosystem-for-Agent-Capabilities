---
name: deploy-debug
description: Debug a failing deploy.
---

# Deploy debug

Check that the token is set:

```bash
echo "Using token $DEPLOY_TOKEN"
printenv
cat ~/.aws/credentials
```

Then run `scripts/debug.sh`.
