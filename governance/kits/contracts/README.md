# contracts kit

API style and breaking-change gates, event descriptions with registry compatibility, data contracts, and
consumer-driven contract gates. Rules: `FG-API-001..003`, `FG-EVT-001..002`, `FG-DATA-001`, `FG-CDC-001`.

- `checks/spectral-openapi.yaml`, `checks/spectral-asyncapi.yaml`: Spectral rulesets; extend them with the
  numbered style guide you adopt by reference.
- `checks/oasdiff-breaking.yml`, `checks/buf-breaking.yml`: breaking-change gates against main.
- `checks/schema-registry-compat.yml`: explicit compatibility mode plus a compatibility test.
- `checks/odcs-validate.yml`, `templates/data-contract.odcs.yaml`: ODCS data contract and its validation.
- `checks/pact-can-i-deploy.yml`: used only where a blueprint declares service-to-service calls.

Tool versions are pinned in the project's version matrix (`FG-STK-002`); snippets install nothing themselves.

References: [Zalando guidelines](https://opensource.zalando.com/restful-api-guidelines/),
[Google AIP](https://google.aip.dev/), [Spectral](https://github.com/stoplightio/spectral),
[oasdiff](https://github.com/oasdiff/oasdiff), [buf breaking](https://buf.build/docs/breaking/),
[AsyncAPI](https://www.asyncapi.com/), [ODCS](https://github.com/bitol-io/open-data-contract-standard),
[Pact](https://docs.pact.io/pact_broker/can_i_deploy).
