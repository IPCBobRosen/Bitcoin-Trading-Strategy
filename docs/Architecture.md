## Development, Staging, and Production Environments

BTS uses separate environments to reduce the possibility of accidental live
trading during development and testing.

### Development

Development is used for writing code, unit testing, simulation, and fake broker
testing. It does not connect to a live CQG account.

### Staging

Staging is a full test environment that closely reproduces production behavior.

It uses:

- Test API credentials
- A staging WebSocket and REST API
- Test trading signals
- CQG demo or simulated execution
- Production-like logging, validation, replay, and reconciliation

Staging must never route an order to a live CQG account.

### Production

Production is the live environment. It receives live trading signals and may
route approved orders to the live CQG account.

### Environment Safety Rule

All environment identifiers must agree before an order may be submitted:

- Incoming message environment
- API credential environment
- BTS application mode
- CQG endpoint
- CQG account ID

If any value does not match, BTS must reject the instruction and generate an
alert.