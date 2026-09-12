# Retained gaming data recovery

The user authorized a fresh collection on September 12, 2026. The latest dataset is
`data/staging/refresh_20260912T201854Z/gaming_current.sqlite`: **19,806 observations**,
34 state/product series, and 1,035 verified referenced source files. Its SHA-256 is
`751e135c083f66d025ca4d03116021215ac136518ba24b2093f135af044df3ee`.
Notebooks 90 and 94–96 read this capture. See the [workflow guide](flut_workflow.md)
for collection exceptions and the independently restored data backup.

The initial 19,749-row rebuild at
`data/staging/rebuild_20260912T191234Z/gaming_current.sqlite` remains unchanged.
The latest run copied that database before refreshing MA, MI, NY and KY; the other
retained series were not rechecked by that selected run. The
[initial rebuild record](data_rebuild_20260912.md) remains the record of the broad
collection and its gaps. The 57 added rows are bounded Kentucky operator/total
transcriptions, not 57 newly reported periods.

This recovers a usable current research dataset. It does not recreate any of the
exact historical snapshots below or their point-in-time availability. Notebooks
91–93 retain their original approval gates and historical outputs.

Code and fixtures were restored on September 12, 2026. The original data archive
was not found in the inspected researchOS, Documents, or Codex storage locations.
Saved notebook output and old manifests are historical evidence, not a usable database.

The separate Pennsylvania and Colorado review worktrees did retain 20 sample
reports and metadata files. They were copied unchanged into `data/raw/PA/` and
`data/raw/CO/` and backed up during integration. These bounded notebook samples
do not restore the missing original/staging databases or the full raw archive.

Restore these exact files relative to `gaming/` when a backup is available:

| Role | Path | Expected SHA-256 |
| --- | --- | --- |
| Original | `data/gaming.sqlite` | `62afd2b97459f151e62fbbf24c7e0d0fe5a52e9dee829931d2554b9b1529d388` |
| Staging study | `data/staging/gaming_nationwide.sqlite` | `023ca5e8e4c16ff0981a2783dabcedf9399939e0241701b0394bc0277eff6ce9` |
| Separately approved corrections | `data/staging/gaming_candidate_20260907_approved_corrections.sqlite` | `492089beb85c1dd802669fea8f214ea7f1afb77265fd44f44c4ec2ca3d4e8d19` |

Also restore the matching `data/raw/` files and their metadata. The dated layout
manifest recorded 1,572 raw files. A matching database hash alone does not restore
the linked report bytes. Validate source paths and hashes, SQLite integrity, and
the exact notebook approval gates after restoring the archive.

Do not create an empty database at these paths or use new downloads as substitutes.
A separately authorized new collection belongs in a new dated staging destination.
Do not change notebook approval hashes to accept that new dataset.
