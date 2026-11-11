# Qualify and publish packages

Qualify exact artifacts before publishing them. Use the qualification policy and
compatibility contract alongside this procedure. The workflows build and test packages;
the publishing jobs upload only after their event and qualification checks pass.
Creating release tags and GitHub releases remains a separate maintainer action.

## Prepare a candidate

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
The candidate qualification workflow performs no publisher registration, distribution
upload or public release. TestPyPI publication is a separate manual choice below.

## Manually test publication on TestPyPI

The separate publish-test.yml workflow matches the pending Trusted Publisher's workflow
filename and environment testpypi. It is workflow_dispatch-only. It does not publish
on pushes, PRs or tags. Production publish.yml uses version-tag pushes for upload,
as described below.

Run a validation first:

```sh
gh workflow run publish-test.yml --ref main -f expected_version=0.5.0 -f publish=false
```

The run calls the full candidate workflow, then verifies the passed manifest's version,
commit, run ID, clean source and hashes. A fresh staging directory gets only the exact
qualified wheel and source distribution. Evidence sidecars and stale/unexpected files
are refused. The build/qualification jobs receive no OIDC permission. No file is rebuilt
between qualification and publication.

After explicit authorization to make the TestPyPI upload, use:

```sh
gh workflow run publish-test.yml --ref main -f expected_version=0.5.0 -f publish=true
```

That choice enables the final job, bound to the GitHub environment testpypi, with
id-token: write. The official PyPA action uses Trusted Publishing, attestations and the
fixed https://test.pypi.org/legacy/ endpoint; no stored PyPI password/token is required.
The publishing job downloads staged artifacts only and does not check out or build
application code. Uploads from branches other than main fail preflight. Concurrent
uploads for the same ref do not run together; existing distributions cause a failure
rather than being silently accepted. TestPyPI does not permit replacing a published file with changed
bytes, so choose a new version if a previous upload must change.

The pending publisher must name distribution mccann-hub-yhmf, repository
jabez007/you-have-mumps-friend, workflow publish-test.yml and environment testpypi.
A TestPyPI publisher registration is separate from GitHub's environment configuration.
GitHub can create the referenced environment when a workflow uses it; configure its
branch/reviewer protections in repository Settings as appropriate before an upload.
This workflow does not change those protection settings. See the
[PyPA action documentation](https://github.com/pypa/gh-action-pypi-publish) for current
Trusted Publishing support. Uploading to production PyPI, creating release tags and
creating public GitHub releases remain separate, unauthorized actions.

TestPyPI is a separate index and may not contain the application's dependencies. After
a successful upload, install the selected framework without resolving dependencies from
that index, then install its qualified dependencies from production PyPI separately.
Do not treat a validation-only run as proof of the publisher/OIDC exchange or index
installation; those occur only after an actual upload.

## Publish an authorized version tag to PyPI

Production publish.yml uploads only on pushes of v* tags, such as v0.5.0. Its guard
requires the tag to match pyproject.toml and mccann_hub.yhmf.__version__ exactly. It
also requires a ## <version> changelog heading and a tagged commit reachable from
fetched origin/main. Prerelease versions use their exact Python packaging spelling,
for example v0.6.0rc1. Review and move applicable Unreleased notes before tagging;
the heading check does not establish that release notes are complete.

Before the first upload, configure a production PyPI Trusted Publisher or pending
publisher for distribution mccann-hub-yhmf with these exact values:

| Setting | Value |
| --- | --- |
| Repository owner | jabez007 |
| Repository | you-have-mumps-friend |
| Workflow filename | publish.yml |
| Environment | pypi |

Configure the GitHub environment pypi with release-tag restrictions and reviewer
protections appropriate to the repository. Production PyPI registration is separate
from the successful TestPyPI registration. See the
[Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

To validate the workflow without creating a tag or uploading, run:

```sh
gh workflow run publish.yml --ref main
```

Manual dispatch checks the two package version declarations, calls full candidate
qualification and stages the exact verified wheel/source pair as pypi-distributions.
It always skips the upload job, even when manually dispatched on a tag. It does not
test a tag-push event, production OIDC authentication or a production installation.

After explicit release authorization and review of the final committed revision,
create and push its annotated version tag. That event runs the tag guard, repeats
full qualification and stages distributions verified against the current commit,
run ID, version and hashes. The upload job downloads only pypi-distributions and uses
the pinned PyPA action, the fixed https://upload.pypi.org/legacy/ endpoint and
attestations. Only this job receives id-token: write and uses environment pypi.
No artifacts are rebuilt after qualification; duplicate files fail rather than
being silently skipped. The workflow does not create or move tags or GitHub releases.
