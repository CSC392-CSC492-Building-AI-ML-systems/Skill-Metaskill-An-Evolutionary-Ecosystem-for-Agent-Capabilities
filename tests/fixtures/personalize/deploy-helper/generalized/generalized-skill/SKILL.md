---
name: deploy-helper
description: Deploy the payments service and email the owner when it finishes.
---

# Deploy helper

Deploys the payments service from {{config.repository_url}}
and emails {{config.contact_email}} with a summary.

## Steps

1. Clone the repository into `{{config.workdir}}`.
2. Run `scripts/deploy.sh`, which builds on {{config.build_host}}.
3. Send the summary to {{config.contact_email}}.

Template syntax such as {{ name }} or ${HOME} belongs to other tools and is left alone,
as is {{config.Not_Valid}}, which is not a placeholder.
