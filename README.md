# loc-tree

`loc_tree.py` is a repo scanner that:

- counts LOC (non-blank lines by default)
- estimates tokens (`ceil(char_count / chars_per_token)`)
- tracks file/directory size
- aggregates totals per directory (`root`, `subdirs`, `total`)
- lists files in each directory (including non-counted extensions)
- provides a tree view and an ncdu-like interactive browser

## Requirements

- Python 3.10+
- Terminal with `curses` support for interactive (`--mode ncdu`)

## Quick start

```bash
cd /Users/artwielogorski/prv/loc-tree
python3 loc_tree.py /path/to/repo --mode tree
python3 loc_tree.py /path/to/repo --mode ncdu
```

## Usage

```bash
python3 loc_tree.py [root] [options]
```

### Important options

- `--mode {ncdu,tree}`: output mode (default: `ncdu`)
- `--chars-per-token N`: token ratio (default: `4.0`)
- `--include-blank-lines`: include blank lines in LOC
- `--all-dirs`: show zero-metric directories
- `--extensions .php,.py,...`: counted code extensions
- `--exclude-dir NAME_OR_PATH`: exclude directory by name/path (repeatable)
- `--exclude-path-regex REGEX`: regex-exclude file/dir by relative path or name (repeatable)
- `--include-hidden`: include dotfiles/dot-directories

## Defaults

### Counted extensions

Default counted extensions:

- `.php`
- `.py`
- `.js`
- `.sh`
- `.twig`
- `.phtml`
- `.tf`
- `.yaml`
- `.yml`

### Default regex path excludes

Default regex excludes are applied automatically:

- `.*jquery.*`
- `.*min\.js$`
- `.*android.js$`

You can add more with `--exclude-path-regex`.

## Examples

### Tree report with custom extensions

```bash
python3 loc_tree.py /path/to/repo --mode tree --extensions .php,.sql,.twig
```

### Exclude minified/vendor-style files

```bash
python3 loc_tree.py /path/to/repo \
  --exclude-path-regex '.*bundle.*' \
  --exclude-path-regex '.*vendor/legacy/.*'
```

### Count only shell scripts

```bash
python3 loc_tree.py /path/to/repo --extensions .sh
```

## Interactive mode keys (`--mode ncdu`)

- `j/k` or `Up/Down`: move
- `Enter` or `Right`: open directory
- `Left` / `Backspace`: go to parent
- `s`: cycle sorting (`tokens`, `lines`, `size`, `name`)
- `q`: quit

Interactive table includes:

- `Lines`, `Tokens`, `Size`
- `LOC Bar`, `Token Bar`
- `Counted` status for files (`yes`/`no`)

## Output semantics

- Directory metrics:
  - `root`: direct files in that directory (`lines`/`tokens` for counted extensions, `size` for all listed files)
  - `subdirs`: sum of all descendant directories
  - `total`: `root + subdirs`
- `Size` is accumulated for all listed files, including files excluded from LOC/token counting by extension.
- Files with non-counted extensions are still listed with `0 lines, ~0 tokens`, their size, and marked as excluded extension.
