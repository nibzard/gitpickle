# Deterministic fixtures

Run the seeded generator through the workspace command:

```sh
cargo xtask fixtures --fixture empty-and-tiny --output fixtures/generated/example
cargo xtask fixtures --fixture git-100m --output fixtures/generated/git-100m
cargo xtask fixtures --fixture lfs-2g --profile smoke --output fixtures/generated/lfs-smoke
```

The output directory must not exist. Generated files stay under an ignored directory.
The generator does not change host Git configuration. It disables inherited Git configuration and hooks.
Git and Git Large File Storage (LFS) must be installed for benchmark environment discovery.

The generator uses seed `20261015` by default. Set `--seed` to select another seed.
Content comes from SHA-256 counter blocks. Identities, dates, hash format, refs, and pack thread counts are fixed.
The manifest includes the generator version, Git version, seed, profile, refs, symbolic HEAD, and digest.
Each repository records actual pack bytes, pack hashes, object counts, and logical blob bytes.
The pack-to-logical ratio discloses compressibility. LFS bytes are reported separately.
Git versions can change pack representations. Compare only matching fixture digests.

| Fixture | Full profile |
| --- | --- |
| `empty-and-tiny` | An unborn repository and a repository with three commits, two branches, and an annotated tag |
| `git-100m` | 1,000 commits and 100 MiB of generated content |
| `git-1g` | 2,000 commits and 2 GiB of content, half zero bytes, targeting about 1 GiB packed |
| `git-10g` | 10,000 commits, 1,000 additional branches, and 10 GiB of generated content |
| `lfs-2g` | Eight actual 256 MiB objects, valid LFS pointers, and attributes |
| `portfolio-404` | 400 repositories with three commits; four with 30 commits and 1–4 MiB of content |
| `history-rewritten` | A retained pre-rewrite bundle, replacement root commit, and deleted branch and tag |
| `failure-matrix` | Interrupted upload, lost response, throttling, credential loss, and corruption cases |

Size labels are targets. Read measured values from `manifest.json`.
LFS payloads use a digest-addressed fixture directory rather than a remote LFS server.
The history manifest records both historical refs and current refs.

The `smoke` profile reduces the three large Git fixtures and LFS objects.
It preserves 404 repositories in the portfolio. Its digest differs from the full profile.
Smoke results cannot establish full-size performance. Allow at least 70 GiB of free disk for a full `git-10g` benchmark.
Generation and benchmark setup can temporarily retain several complete copies.
