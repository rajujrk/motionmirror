# Contracts

The only coupling between languages. `fixtures/` is run by JUnit and pytest, so Java and Python accept and reject the same inputs.

| Path | Purpose |
| --- | --- |
| `messages/task-envelope.v1.schema.json` | RabbitMQ task message |
| `settings/generation-settings.v1.schema.json` | Generation settings |
| `asyncapi.yaml` | Broker topology and message contract |
| `openapi.json` | Generated from the API by `make contracts` |
