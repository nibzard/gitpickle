# Contract tests

`cli_contract.rs` tests the built executable through its process boundary.
The executable manifest registers this file because the root manifest is a virtual workspace.

Run all checks with `cargo xtask check`. Run the contract alone with:

```sh
cargo test --locked --package gitpickle --test cli_contract
```

Add compatibility, recovery, and security contracts as those features become available.
Current contracts verify help, version output, error streams, and unsupported-command failure.
They do not establish backup or recovery correctness.
