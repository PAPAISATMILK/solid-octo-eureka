PC remote agent: setup guide (Windows)

What it does: your PC checks a private command queue every few seconds. You can ask it to lock the PC, power it off after 30 seconds, cancel a pending shutdown, or open an app from your list. It opens no ports on your router, and it can only open apps listed in apps.json.

Files
- pc_agent.py: the agent that runs on your PC.
- config.json: your server address and secret token.
- apps.json: the whitelist. Only these names can be opened. Add your own as "name": "full path to the .exe". Use double backslashes inside the quotes.

Steps
1. Install Python 3.10 or newer from python.org. Tick "Add python to PATH" in the installer.
2. Put the pc_remote folder somewhere permanent, for example D:\pc_remote (not Downloads).
3. Make a token: open PowerShell and run
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   Paste the output into config.json as "token". Keep it private.
4. Set "server_url" in config.json to the command endpoint address you were given. It must start with https://.
5. Edit apps.json so each path points to the .exe on your PC. To find one, right-click the app's shortcut, choose Open file location, and copy the .exe path.
6. Test without doing anything: in PowerShell, inside the pc_remote folder, run
   python pc_agent.py --dry-run
   It should only print what it would do.
7. Run it for real: python pc_agent.py. Leave the window open, or see step 8.
8. To start it at login: press Win+R, type shell:startup, and put a shortcut there that runs
   cd /d D:\pc_remote && pythonw pc_agent.py
   pythonw runs without a window. The log is pc_agent.log in the same folder.

Stopping it
- Close the window, or end pythonw.exe in Task Manager.
- To cancel a shutdown already started: run shutdown /a in any command prompt, or send the cancel_shutdown command.

Safety notes
- Anyone with your token can lock or shut down your PC. Do not share config.json.
- The shutdown delay is fixed at 30 seconds in the code.
- Anything not on the whitelist is refused. The agent never runs arbitrary commands.
- Save your FL Studio project before sending a shutdown.
