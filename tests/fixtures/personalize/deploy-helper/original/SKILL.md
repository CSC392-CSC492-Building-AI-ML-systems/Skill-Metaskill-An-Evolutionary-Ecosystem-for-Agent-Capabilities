---
name: deploy-helper
description: Deploy the payments service and email the owner when it finishes.
---

# Deploy helper

Deploys the payments service from https://github.com/acme-example/payments-service
and emails jane.doe@acme-example.dev with a summary.

## Steps

1. Clone the repository into `/Users/janedoe/work/payments`.
2. Run `scripts/deploy.sh`, which builds on build.acme.internal.
3. Send the summary to jane.doe@acme-example.dev.

Template syntax such as {{ name }} or ${HOME} belongs to other tools and is left alone,
as is {{config.Not_Valid}}, which is not a placeholder.
