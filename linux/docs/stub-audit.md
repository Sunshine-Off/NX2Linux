# Which not ported functions the game calls (issue #20)

Some SDK functions are not ported to the Linux host. The host answers 0 and logs the first call of each (`[stub] ... is not ported, answers 0`). The issue counts 244 of them for Xenoblade 2. What nobody knows is which of the 244 the game calls when you play for hours, how often, and from where. This audit records it, so the next ports are chosen by use.
**No Xenoblade 2 run has used it yet.** Everything below is checked with fake data only, and the numbers it will give are unknown.

## What the host records

`NX2LINUX_STUB_AUDIT=FILE` writes a JSON file about the stubs the build exports and the ones the run called:

| Field | Meaning |
|---|---|
| `stubbed_exported` | The not ported functions this build exports. It is the list the audit is about, so that a function nobody called can be told from one that is not in the build |
| `calls` | For each function called: `count`, `first_site` (module and offset of the caller, such as `main+0x1a2b3c`) and `first_s` (second of the run of the first call) |
| `seconds`, `frames`, `title` | How long the run was, the frames presented, the title ID |

The file is rewritten every `NX2LINUX_STUB_AUDIT_INTERVAL` seconds (30 by default) and at the end of the report, through a temporary file and a rename, so a run that is killed leaves a whole file that is at most one interval old. It holds SDK symbol names that are in this repository, counts and offsets into the game's modules. It has no name of a game function, no memory and no path. Off unless the variable is set.

The report at the end of a run now says `stubbed overrides called: N of M exported, most called first:` with the count of each.

**No count is lost between threads.** A lock makes two guest threads that call the same stub at once count both. The cost of that lock on a hot stub is deduced as negligible, a stub call already crosses ctypes, and is not measured.

## Before the runs

1. Get the engine of this change: `git switch shikosu/p3-b-stub-audit` and `python3 tools/engine.py sync`, or `main` once it is merged.
2. The change is in the host and not in the generated code, so **no recompilation is expected** (a deduction). If the run starts building functions, stop it and tell me.
3. `source ~/xc2-env.sh` in every new terminal. Use `tee -i` so that Ctrl-C does not kill the log.

## The runs

The issue depends on a long play through the game (P5-b), so it stays open until the runs cover it. One run per part of the game is more useful than one long run in one place, because a function that only a menu calls shows up only when you open the menu. Suggested parts, in this order: the title screen and the menus, walking in a field, a battle, a cutscene, the item and equipment screens, saving and loading, the settings. Several parts can share a run.

For each run, a different file:
```sh
source ~/xc2-env.sh
export NX2LINUX_STUB_AUDIT=$HOME/audit-1.json
XC2_SECONDS=3600 bash ~/xc2-run.sh 2>&1 | tee -i ~/audit-1.log
echo "exit=${PIPESTATUS[0]}"
```
Use `audit-2.json`, `audit-3.json` and so on for the next ones. One hour a run is a proposal, a shorter run is fine if it covers its part.
If the pull request of the clean shutdown (#22) is merged, Ctrl-C gives a final report and a final write. Before it, Ctrl-C leaves the last copy of the file, up to 30 seconds old.

**Time:** the time you play. **Disk:** the file is probably 30 to 60 kB (a guess, about 250 names and the calls). The log is unknown, see `linux/docs/soak-test.md`.

## Reading the result

```sh
python3 tools/stub_audit.py ~/audit-*.json --never
```
It prints the number of stubbed functions this build exports and how many were called, a table by family (`nn::hid`, `nn::audio`, ...), the 20 most called with the runs that called them and the first call site, and with `--never` the ones nobody called. `--top N` changes the 20, `--json FILE` keeps the merged view.

**Note:** the whole output of the command, which parts of the game each run covered, and anything odd such as a function called thousands of times a second.

## What the result decides

- A function called often is the one whose answer of 0 may be wrong for the game. Port those first, the family table says where they cluster.
- A function that no run called is not proven useless. It says that these scenes did not need it, and the list of scenes is part of the result.
- `first_site` gives the caller in the game's modules. It is the start of a look at why the game calls it, with the survey tool.

## What is not covered

- Functions that are not stubs: an SDK function the host does not export at all, a missing NVN call (`nvn:` in the report) and a kernel call the host does not implement. They have their own lines in the report.
- The behaviour of the game after the stub answered 0. The audit says that a call happened, not what it broke.
