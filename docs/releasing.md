# Prepare a candidate

This process builds and qualifies artifacts. It does not publish them, create a Git tag
or create a GitHub release. Use the qualification policy and compatibility contract
alongside this procedure.

1. Select a clean committed revision and its package version. Keep pyproject.toml and
   mccann_hub.yhmf.__version__ equal. Review Unreleased notes and the supported upgrade
   path. The historical 0.5.0 delivery report is not a record of a published release.
2. Run the locked checks from CONTRIBUTING. Build into a clean output directory:

   ```sh
   uv build --out-dir dist/candidate
   uv run python scripts/check_artifacts.py dist/candidate --expected-version 0.5.0
   uv run python scripts/qualify_install.py dist/candidate/mccann_hub_yhmf-0.5.0-py3-none-any.whl
   uv run python scripts/qualify_install.py dist/candidate/mccann_hub_yhmf-0.5.0.tar.gz
   ```

3. Run the manual Qualify release candidate Actions workflow on that revision with its
   exact expected_version. It calls the same mandatory matrix as Checks, including
   installed native source/wheel tests on both engines and journal modes. Its final
   evidence job verifies version, commit, clean source tree and SHA256SUMS, then marks
   qualification_status as passed. The initial build manifest stays pending until
   the mandatory qualification has actually succeeded. No write
   permission to releases or OIDC publishing token is granted.
4. Download candidate-distributions and qualified-candidate before their 90-day expiry.
   Retain qualification.json, SHA256SUMS, lockfile, test logs and environment information.
   Check hashes after downloading. A failed, skipped or cancelled mandatory job blocks
   qualification. An artifact built from dirty/untracked packaged inputs is local
   development evidence only, even if it works.
5. Review independent consumer and historical upgrade results. Preserve the old artifact,
   application declarations and generated M routines. Rehearse cutover on a backup copy.

Builds use hatchling through uv's PEP 517 environment. The source whitelist excludes
agent state, caches and credentials. The audit requires the exact license, M templates,
namespace and py.typed marker, and checks dependency/version metadata. Clean installers
use temporary directories, fresh virtual environments and isolated Python. They reject
editable/source-tree imports. The native extra installs YDBPython, not YottaDB itself.
The native harness creates a disposable database and removes it after qualification.

## Intentional maintainer actions

Actual publication requires a separate explicit authorization. Before it, choose the
final version, move applicable Unreleased notes into that version's release notes,
qualify the exact final commit again, and review unresolved licensing/deployment terms.
Do not retag or overwrite already distributed artifacts. The maintainer may then create
an annotated version tag, push it, attach the qualified artifacts and hashes to a
release, and publish those exact files. These steps are deliberately absent from the
preparation workflow.

For PyPI, configure a protected environment and an exact Trusted Publisher binding
for the authorized repository, workflow and environment. Use OIDC instead of a stored
upload token. A pending publisher can create a first project but does not reserve its
name. Follow the [PyPI instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and the [packaging workflow guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).
No publisher, project reservation, upload or release action is performed by this work.
