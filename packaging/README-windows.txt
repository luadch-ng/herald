Herald  -  ADC release announcer
=============================================

WINDOWS  -  no installation, no Python needed.

1. Unzip this whole folder somewhere (Desktop is fine).
2. Double-click  Herald.exe  to open the GUI.
3. In the GUI:
     - Left sidebar : your hubs (one demo hub is preconfigured).
     - Config tab   : hub address, port, nick, password, keyprint,
                      and the "Enabled" checkbox (for the headless runner).
     - Rules tab    : add a watch folder + a category, tick "active".
     - Status tab   : click Connect. The LED turns green once logged in.
   Your settings are saved in the  data\  folder next to Herald.exe
   (portable - move the whole folder and it keeps working).

Run it WITHOUT the GUI (optional - e.g. to leave it running):
   Open a terminal (cmd/PowerShell) in this folder:
     herald-cli.exe --list     show your hubs + which are enabled
     herald-cli.exe --check    verify each rule's watch folder exists here
     herald-cli.exe            run every enabled hub  (Ctrl-C to stop)

Good to know:
 - The hub's self-signed TLS cert is trusted by KEYPRINT (paste the
   SHA256/... from your hub into the Keyprint field, or leave it empty
   to trust on first connect).
 - Completion detection: a release is only announced once it is fully
   written - either its SFV is complete, or the folder has been quiet
   for N seconds (per-rule, Rules tab -> Completion).
 - Running on a Linux server instead? Set Settings -> Target runtime OS
   to "Linux" first, then enter the Linux paths. See the source package
   / README for the headless + systemd guide.

This is an early TEST build. Bugs and feedback are very welcome - thanks
for trying it!
