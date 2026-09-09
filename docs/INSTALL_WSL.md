# Windows setup — WSL2 (for first-time WSL users)

These tools run on **Linux or macOS**. On Windows you run them inside **WSL2**
(Windows Subsystem for Linux) — a real, lightweight Ubuntu Linux that runs
alongside Windows, no dual-boot or virtual machine to manage. You install WSL2
once, then follow the normal Linux install for the suite.

This guide assumes you have **never used WSL**. It matches the current Microsoft
instructions (see [Microsoft's install page](https://learn.microsoft.com/windows/wsl/install)
if you want the source).

## What you need first

- **Windows 11**, or **Windows 10 version 2004 or newer** (Build 19041+). To
  check: press <kbd>Win</kbd>+<kbd>R</kbd>, type `winver`, press Enter.
- **Administrator rights** on the PC — but only for the one-time install below.
  Day-to-day use afterward is your normal (non-admin) account.
- An internet connection. Hardware virtualization is normally already on; if the
  install later complains, see [Troubleshooting](#troubleshooting).

## Step 1 — Install WSL2 (one time, as Administrator)

1. Click **Start**, type **PowerShell**, right-click **Windows PowerShell**, and
   choose **Run as administrator**. (Click **Yes** on the permission prompt.)
2. In that window, run:

   ```powershell
   wsl --install
   ```

   This turns on the needed Windows features and installs **Ubuntu** (the default
   Linux) with **WSL2**.
3. **Restart your PC** when it asks.
4. After the restart, an **Ubuntu** window *may* open by itself and unpack
   itself (a minute or two the first time). Often it doesn't, and the PC just
   comes back to your normal desktop with nothing to show for it. Nothing is
   wrong — see [Step 1a](#step-1a--no-ubuntu-window-after-the-restart) to open
   it yourself.
5. Once the Ubuntu window is open, it asks you to create a **Linux username and
   password** — pick any you like and **write the password down**; you'll type it
   for `sudo` commands. (This is separate from your Windows login.) The username
   can be lowercase letters only; the password is invisible as you type it, which
   is normal.

> If `wsl --install` just prints its help text, WSL is already partly present —
> run `wsl --update`, then `wsl --install -d Ubuntu`.

## Step 1a — No Ubuntu window after the restart

Skip this if Ubuntu already opened and asked for a username. Otherwise open it
yourself; it is one command.

1. Open PowerShell again: click **Start**, type **PowerShell**, click **Windows
   PowerShell**. A plain (non-admin) window is fine this time.
2. Type this and press Enter:

   ```powershell
   wsl
   ```

3. What you see next tells you where you are:

   - **A new prompt ending in `$`** (e.g. `you@YOUR-PC:~$`) — that *is* Ubuntu,
     running inside the PowerShell window. There is no separate window to find.
     Go back to Step 1, item 5, or on to Step 2.
   - **A username prompt** (`Enter new UNIX username:`) — the unpacking finished
     but nobody answered it. Answer it now (Step 1, item 5).
   - **"Windows Subsystem for Linux has no installed distributions"**, or
     `wsl` isn't recognised at all — the restart turned the Windows features on
     but never installed Ubuntu. Install it explicitly:

     ```powershell
     wsl --install -d Ubuntu
     ```

     This unpacks Ubuntu and asks for the username and password. No second
     restart is needed.
   - **Some other error** — run `wsl --update` in an **administrator**
     PowerShell (Step 1, item 1), then type `wsl` again.

Two other ways to reach the same Ubuntu, once it is installed:

- **Start** menu → type **Ubuntu** → Enter. It appears there like any other app,
  and you can right-click it → **Pin to Start** / **Pin to taskbar**.
- **Windows Terminal** (Windows 11's terminal app) → the **˅** arrow next to the
  tab bar → **Ubuntu**.

To leave Ubuntu and get PowerShell back, type `exit`.

## Step 2 — Update and verify

In PowerShell (normal, non-admin is fine):

```powershell
wsl --update          # get the latest WSL engine
wsl -l -v             # list distros + WSL version
```

`wsl -l -v` should show your distribution with **VERSION 2**, e.g.:

```
  NAME      STATE           VERSION
* Ubuntu    Running         2
```

If a distro shows **VERSION 1**, upgrade it:

```powershell
wsl --set-default-version 2
wsl --set-version Ubuntu 2
```

## Step 3 — Open your Linux terminal

Open **Ubuntu** from the Start menu, or type `wsl` in PowerShell — either way
you land in the same place ([Step 1a](#step-1a--no-ubuntu-window-after-the-restart)
if you can't find it). You now have a Linux command line. A few things worth
knowing:

- Your Linux home is `~` (e.g. `/home/<you>`). **Keep project files here**, not on
  the Windows `C:` drive — it's much faster.
- Your Windows drives are visible under `/mnt/c/…` if you ever need them.
- `sudo <command>` runs something as admin *inside Linux* and asks for the Linux
  password you set in Step 1.

## Step 4 — Install the diagnostic tools

From that Ubuntu terminal, follow the **Linux / personal computer** path in the
[main README](../README.md#-quick-start--personal-computer-linux--macos--wsl2):
install `git` and a conda/Miniforge, then run `bin/bdtools install all`. From
here on, WSL2 behaves exactly like any other Linux machine — the rest of this
suite's docs apply unchanged.

## Permissions — what actually needs admin

| Action | Rights needed |
|---|---|
| `wsl --install` (the one-time setup in Step 1) | **Administrator** (elevated PowerShell) |
| `wsl --update` | Administrator on some builds; try normal first |
| Everything after (running Ubuntu, `bdtools`, the tools) | Your **normal** account |
| Installing Linux packages *inside* Ubuntu (`apt`, conda) | `sudo` + your **Linux** password (not your Windows one) |

## Troubleshooting

- **"Virtualization" / "please enable the Virtual Machine Platform" error.**
  Reboot into your PC's firmware (BIOS/UEFI) and enable virtualization
  (often called *Intel VT-x*, *AMD-V*, or *SVM*), then retry `wsl --install`.
- **No Ubuntu window, and no Ubuntu in the Start menu, after the restart.**
  See [Step 1a](#step-1a--no-ubuntu-window-after-the-restart) — the usual cause
  is that the features were enabled but the distribution never installed, and
  `wsl --install -d Ubuntu` fixes it.
- **You're on an older Windows 10 build** (before 2004 / 19041). Update Windows,
  or follow Microsoft's
  [manual install steps](https://learn.microsoft.com/windows/wsl/install-manual).
- **See available distributions**: `wsl --list --online`. Install a specific one
  with `wsl --install -d <DistroName>`.
- **Confirm WSL is healthy**: `wsl --status` and `wsl --version`.
