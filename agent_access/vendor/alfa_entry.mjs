// Siteimprove Alfa (MIT) for the browser: agent_access/probes/alfa.py injects the bundle and calls window.__alfa.
// Rebuild (pinned versions; see ALFA_LICENSE.txt):
//   npm install @siteimprove/alfa-{act,rules,web,dom,device,http}@0.119.0 esbuild@0.28.2
//   npx esbuild alfa_entry.mjs --bundle --minify --format=iife --platform=browser --outfile=alfa.min.js
import { Audit, Outcome } from "@siteimprove/alfa-act";
import rules from "@siteimprove/alfa-rules";
import { Page } from "@siteimprove/alfa-web";
import { Element, Node } from "@siteimprove/alfa-dom";
import { Native } from "@siteimprove/alfa-dom/native";
import { Device } from "@siteimprove/alfa-device";
import { Request, Response } from "@siteimprove/alfa-http";

const wcag = (rule) => [...rule.requirements].map((r) => r.chapter).filter((c) => /^[1-4]\.\d+\.\d+$/.test(c || ""));

window.__alfa = {
  version: "0.119.0",
  rules: () => [...rules].map((r) => ({ id: r.uri.split("/").pop(), criteria: wcag(r) })),
  // Failed outcomes only: Alfa is a second opinion, and its cantTell is a question the auditors already ask.
  audit: async () => {
    const device = Device.standard();
    const page = Page.of(Request.empty(), Response.empty(), Node.from(await Native.fromNode(document), device), device);
    const out = [];
    for (const o of await Audit.of(page, rules).evaluate()) {
      if (!Outcome.isFailed(o) || !wcag(o.rule).length) continue;
      const failed = (o.toJSON().expectations || []).filter(([, r]) => r.type === "err");
      out.push({ rule: o.rule.uri.split("/").pop(), criteria: wcag(o.rule),
                 target: typeof o.target?.path === "function" ? o.target.path() : null,
                 message: failed.length ? String(failed[0][1].error?.message || "") : "" });
    }
    return out;
  },
};
