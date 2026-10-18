// Line-delimited JSON bridge between Python and NVDA (via Guidepup).
//
// Python sends one command per line, e.g. {"id": 1, "cmd": "next"}, and reads one reply per line:
// {"id": 1, "ok": true, "result": ...}. Commands map to Guidepup's NVDA API.
//
// The browser window must be in the foreground: NVDA reads whatever window has focus.
import readline from "node:readline";
import { nvda, windowsActivate } from "@guidepup/guidepup";

// Browse-mode moves Python uses: read the next line / jump to the next heading.
const NAVIGATION = new Set(["next", "nextHeading"]);

const reply = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");

async function handle({ cmd, args = {} }) {
  switch (cmd) {
    case "activate":
      // Bring the (already running) browser window to the OS foreground so NVDA reads it.
      await windowsActivate(args.executablePath, args.windowTitle);
      return true;
    case "start": {
      // Guidepup waits 30 s per attempt for NVDA's add-on to come up; a cold start can take longer on a
      // busy machine, so retry. Later attempts are faster because NVDA's files are already cached.
      let lastError;
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          await nvda.start({ capture: "initial" });
          return { attempts: attempt };
        } catch (err) {
          lastError = err;
          await new Promise((r) => setTimeout(r, 3000));
        }
      }
      throw lastError;
    }
    case "stop":
      await nvda.stop();
      return true;
    case "press":
      await nvda.press(args.key);
      return nvda.lastSpokenPhrase();
    case "perform": {
      // Any NVDA keyboard command by name, e.g. "reportCurrentFocus" (NVDA+Tab).
      const command = nvda.keyboardCommands[args.command];
      if (!command) throw new Error(`Unknown NVDA command: ${args.command}`);
      await nvda.perform(command);
      return nvda.lastSpokenPhrase();
    }
    default:
      if (NAVIGATION.has(cmd)) {
        await nvda[cmd]();
        return nvda.lastSpokenPhrase();
      }
      throw new Error(`Unknown command: ${cmd}`);
  }
}

const rl = readline.createInterface({ input: process.stdin });
let stopped = false;
for await (const line of rl) {
  if (!line.trim()) continue;
  let msg;
  try {
    msg = JSON.parse(line);
    reply({ id: msg.id, ok: true, result: await handle(msg) });
  } catch (err) {
    reply({ id: msg?.id ?? null, ok: false, error: String(err?.message ?? err) });
  }
  if (msg?.cmd === "stop" && (stopped = true)) break;
}
// stdin closed without "stop" (Python crashed or was killed): don't leave NVDA running and speaking.
if (!stopped) await nvda.stop().catch(() => {});
process.exit(0);
