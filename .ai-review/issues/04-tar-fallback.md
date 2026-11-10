## Summary
`import_campaign` falls back to an unfiltered `tarfile.extractall` when the `filter=` argument is unsupported. On Python 3.10.0–3.10.11 and 3.11.0–3.11.3, both inside the README's "Python 3.10+" support range, an uploaded archive can then write files outside the campaigns directory through `../` members or symlinks.

## Context
The dashboard's `POST /campaigns/import` (`dashboard.py` `campaign_import`) saves an uploaded `.tgz` and calls `tools.campaign_archive.import_campaign`. That function extracts into a temp dir under `campaigns/`, then renames the campaign folder into place.

Relevant code (pinned to 2965eb7):
- [`tools/campaign_archive.py#L115-L123`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/campaign_archive.py#L115-L123): `import_campaign`:
  ```python
  try:
      tar.extractall(tmp_path, filter="data")
  except TypeError:
      tar.extractall(tmp_path)
  ```
- [`tools/campaign_archive.py#L91-L140`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/campaign_archive.py#L91-L140): the whole function.
- [`README.md#L57-L60`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/README.md#L57-L60): "Python 3.10+".

## Evidence
`filter=` was added in 3.12 and backported to 3.11.4 and 3.10.12. On older interpreters `extractall(..., filter=...)` raises `TypeError: extractall() got an unexpected keyword argument 'filter'`. The `except` branch then extracts with the legacy behaviour, which honours absolute paths, `..` components and outward symlinks (CVE-2007-4559).

## Proposed design
Never extract unfiltered. When `filter=` is unavailable, validate every member manually before extracting:

```python
def _safe_members(tar: tarfile.TarFile, dest: Path) -> list[tarfile.TarInfo]:
    dest = dest.resolve()
    out = []
    for m in tar.getmembers():
        if not (m.isfile() or m.isdir()):
            raise ValueError(f"archive member {m.name!r} is not a regular file or directory")
        target = (dest / m.name).resolve()
        if dest != target and dest not in target.parents:
            raise ValueError(f"archive member {m.name!r} escapes the extraction directory")
        out.append(m)
    return out
```
Use `tar.extractall(tmp_path, filter="data")` when `hasattr(tarfile, "data_filter")`. Otherwise use `tar.extractall(tmp_path, members=_safe_members(tar, tmp_path))`. Feature detection beats catching `TypeError`, which could also mask unrelated errors.

## Implementation steps
1. In `tools/campaign_archive.py`, add `_safe_members` near the top-level helpers.
2. Replace the `try/except TypeError` block in `import_campaign` with the `hasattr(tarfile, "data_filter")` branch described above.
3. Let `ValueError` propagate. The dashboard already maps it to HTTP 400.

## Acceptance criteria
- [ ] Importing an archive containing `../evil.txt` raises `ValueError` and creates no file outside the temp dir, on both code paths. Force the legacy path in tests with `mock.patch.object(tarfile, "data_filter", create=False)` or by deleting the attribute.
- [ ] Importing an archive containing a symlink member raises `ValueError` on the legacy path.
- [ ] A normal export from `export_campaign` round-trips through `import_campaign` unchanged.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
New `tests/test_campaign_archive.py`: build tarballs in a temp dir with `tarfile.open(..., "w:gz")` and `TarInfo`. Patch `campaign_archive._CAMPAIGNS` to a temp dir. Cases: traversal member, absolute path member, symlink member, a valid archive on both paths, and `rename` still working.

## Out of scope
- Do not change `export_campaign`, the dashboard route, or slug validation.
- Do not raise the minimum Python version.

## Risks and gotchas
- `hasattr(tarfile, "data_filter")` is the documented feature test for the backport.
- `dest` must be resolved before comparing, or a symlinked tmp dir (macOS `/var` points to `/private/var`) gives false rejections.

## Dependencies
Blocked by: #1

## Metadata
Type: security | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
