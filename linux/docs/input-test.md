# Inputs: gamepad, sticks, short presses and touch (issue #21)

Before this change the Linux host gave the game only buttons, and three things were wrong in the code. The first two were found by reading it. The third was shown by a test with a virtual gamepad (SDL can attach one without hardware), which fails without the fix. None was seen on Xenoblade 2:

- **Every gamepad button arrived as the B button.** The C code passed `event.key` to Python for all events, and for a gamepad button that field is 0, which Python reads as SDL's south button, Switch B. The real button number was in another field that was never passed on.
- **The host never opened a gamepad.** SDL tells that a gamepad exists (an ADDED event) and sends its buttons and axes only once the program has opened it. The host did not, so a real gamepad may have produced no button event at all, and the first point only mattered for the events that did arrive. It opens each gamepad when SDL announces it now, also the ones plugged in before the start.
- **The sticks were never read.** Axis events arrived without their value and `NpadState` always held zero sticks. With a gamepad or the keyboard the character could not walk in a game that uses the stick.

This change fixes both, and adds the triggers, a way to use the keyboard as sticks, a fix for short presses, and the mouse as a finger on the touch screen. **No Xenoblade 2 run has used any of it yet.** The C part and the Python part are checked with fake events only.

## What the host does now

| Input | What the game gets |
|---|---|
| Keyboard | As before: X A, Z B, C X, V Y, arrows D-pad, Return Plus, Backspace Minus, A and S the shoulders, Q and W the triggers |
| `NX2LINUX_KEYS_STICKS=1` | The arrows are the left stick and I J K L the right stick. The D-pad keys do nothing then. A diagonal is not longer than a straight line |
| Gamepad buttons | The physical layout of SDL: the east button is A, the south one is B. An Xbox pad is therefore A and B swapped against its labels, like a Switch |
| Gamepad sticks | Radial dead zone of 15 percent (`NX2LINUX_DEADZONE`, in percent), rescaled so that full tilt stays full. Up is positive, as on the Switch |
| Gamepad triggers | ZL and ZR as buttons above half way, released under a quarter |
| Focus | SDL keeps the events of a gamepad back while the game window does not have the focus (checked with the virtual gamepad). Click on the window before judging that the gamepad does nothing |
| Several gamepads | The events carry no gamepad number, so two gamepads are mixed into one. The log names the first one seen and says when a second one appears |
| Mouse | The left button is a finger on the touch screen. The window is mapped to 1280 by 720. Other buttons do nothing |
| Short presses | A button released before the game has read it stays visible until it has been read once and for 50 ms (`NX2LINUX_MIN_HOLD`, in ms, 0 gives the old behaviour), and never longer than 500 ms (`NX2LINUX_MAX_HOLD`). A touch gives Start, then End, even for a tap |
| Not provided | Motion sensors, vibration, the mouse and the keyboard as devices of the game. The stub audit (`stub-audit.md`) says whether the game calls them |

What it does not do: two presses of the same button between two reads of the game count as one (the report counts them as `merged`), two gamepads cannot be told apart, and the touch screen has one finger.

The end of every run now prints a line such as:
`input: 840 reads by the game, 31 presses, 4 kept until read, 0 merged, 0 never read, 3 touches (0 never read) | events: 12 key, 58 gamepad button, 5231 gamepad axis, 9 mouse | gamepad 0`
It says whether the events reached the host, whatever the game did with them.

## Before the runs

1. Rebuild the host libraries, this change touches the C code: `cmake -S linux/host -B ~/.cache/nx2linux/build-host && cmake --build ~/.cache/nx2linux/build-host`. It builds the libraries, not the game.
2. Get the engine of this change: `git switch shikosu/p3-c-input-hold` and `python3 tools/engine.py sync`, or `main` once it is merged.
3. No recompilation of the game is expected, the change is in the host (a deduction). If the run starts building functions, stop it and tell me.
4. `source ~/xc2-env.sh` in every new terminal.

## Run 1: keyboard and mouse, about 10 minutes

```sh
source ~/xc2-env.sh
XC2_SECONDS=600 bash ~/xc2-run.sh 2>&1 | tee -i ~/input-keys.log
grep '^input:' ~/input-keys.log
```
In a menu or any screen where the cursor moves, press one direction as fast as you can 20 times, then the same with a longer press. Click on things that look like they take a finger (the map, a menu). Then run it again with `NX2LINUX_KEYS_STICKS=1` in front of `bash` and try to walk with the arrows.

**Note:** the `input:` line of each run, how many of your 20 quick presses the cursor followed (with the default, then with `NX2LINUX_MIN_HOLD=0` in front of `bash`, which is the old behaviour), whether a click did anything and where, whether the character walked with `NX2LINUX_KEYS_STICKS=1`.

## Run 2: gamepad, about 15 minutes

Plug the gamepad in before the run and click on the game window once it is open. The log has a line `[input] gamepad N connected` once the first frame is on screen. If it is missing, the gamepad was not seen by SDL and nothing below is worth doing.
```sh
source ~/xc2-env.sh
XC2_SECONDS=900 bash ~/xc2-run.sh 2>&1 | tee -i ~/input-pad.log
grep -E '^input:|\[input\]' ~/input-pad.log
grep -i name /proc/bus/input/devices | grep -i -E 'pad|controller|xbox|dual|pro'
```
Go through the buttons one at a time and say what each did: the four face buttons, both shoulders, ZL and ZR, both stick clicks, Plus, Minus, the D-pad. Walk with the left stick, move the camera with the right stick, push each stick all the way in a circle.

**Note:** the model of the gamepad (the last command), the `input:` and `[input]` lines, one line per button or stick saying what the game did with it (nothing, or what), whether the character stops by itself when you let go of the stick (a drift shows up as walking alone, and the dead zone can be changed with `NX2LINUX_DEADZONE=25`), and whether a button that does something wrong is the one you pressed.

## Reading the result

- Events counted in the `input:` line but nothing in the game: the host gave them and the game ignored them. Which functions it reads is a question for the stub audit.
- No events counted: they did not reach the host. The C side is then the place to look.
- A button that does something else than on a Switch: say which. The physical layout is a proposal, a different mapping is a decision for the team.

## What is not covered

- Motion controls, vibration and the Joy-Con.
- The touch screen as more than one finger, and a touch outside the window.
- Whether 50 ms is the right hold. It is a first guess.
