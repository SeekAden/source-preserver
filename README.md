# Source Preserver

Save ordinary source files from HTTPS Git repositories, keeping only the current and previous snapshots.

The main repository's files are preserved, including README files, license texts, hidden files, and directly tracked Git LFS assets. Separate dependency repositories (Git submodules) are **not downloaded**. Their `.gitmodules` references are retained. Third-party files already committed directly to the main repository remain included.

## Requirements

- Git and Python 3.9 or later
- A POSIX shell, such as on macOS or Linux
- Git LFS for repositories that use it

## Usage

Download or clone this tool, then create your repository list:

```sh
cp projects.example.txt projects.txt
```

Edit `projects.txt` to contain one plain HTTPS repository URL per line. Then run:

```sh
sh update-projects.sh --limit 1
```

To process the whole list:

```sh
sh update-projects.sh
```

No downloaded project code is executed. The default branch is cloned shallowly, verified, and exported without its Git database. Downloads are staged before replacement. Unavailable upstreams leave existing snapshots untouched. Source edits in an existing snapshot block replacement; Finder `.DS_Store` bookkeeping files are ignored.

## Archive layout

```text
GitHub/<owner> - <repo>/current/source/
GitHub/<owner> - <repo>/previous/source/
Other/<owner> - <repo>/current/source/
```

Each snapshot has a `metadata.json` recording the source URL, repository owner, project name, commit SHA, dates, license-file paths and checksums, and file checksums. The owner identifies the repository namespace; original authorship notices remain in the source.

`archive-index.csv` lists current snapshots. `warnings.log` describes the latest run. The updater reports `UP TO DATE`, `UPDATED`, and `WARNING`.

Copy the archive after the updater finishes. Symlinks and incompatible Windows paths are flagged; Windows may also require long-path support. Original license terms apply to archived projects. Saving their source does not grant additional rights.

## Recovery and limits

Directory replacement uses recoverable renames rather than a single atomic swap. Avoid copying the archive during an update. Keep a separate backup.

After a forced termination, first ensure the updater has stopped. Remove the empty `.update-lock` directory, then rerun the updater to recover transactions. Leave `.transaction.json` and `.old-*` folders for automatic recovery. Orphaned staging downloads can be removed after recovery.

This preserves the main repository's source, not a complete offline build environment. Separate dependencies, release downloads, issues, pull requests, and package registries are outside its scope.

## Tests

```sh
python3 -B test_preservation.py
```

`remove-submodule-snapshots.py` is a one-time migration utility for archives created by an older version that downloaded submodules. It is also used by the cleanup test. New archives do not require it.

Downloaded repositories, personal repository lists, logs, and snapshots are excluded from this tool's Git repository.
