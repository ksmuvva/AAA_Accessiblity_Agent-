import type { Register } from 'claude-code'

const REPO = 'AAA_Accessiblity_Agent-'

// Guard 1: a top-level `agents` module shadows the OpenAI Agents SDK.
const SHADOW_PATH = new RegExp(`(^|/)${REPO}/agents(\\.py$|/|$)`, 'i')
const CREATES = /(^|[\s;&|(])(mkdir|touch|mv|cp|tee)\s|>/
const SHADOW_DENY =
  'a top-level `agents` module shadows the OpenAI Agents SDK (CLAUDE.md). Put it inside agent_access/.'

/** Whether a shell command that creates files names a top-level `agents` path. */
export const shadowsSdk = (command: string, cwd: string): boolean => {
  if (!CREATES.test(command)) return false

  const isInRepo = cwd.toLowerCase().endsWith(`/${REPO.toLowerCase()}`)

  return command
    .split(/[\s;&|()<>"'=]+/)
    .map(word => word.replace(/^\.\//, '').replace(/\/+$/, ''))
    .some(word =>
      word.startsWith('/') || word.startsWith('~')
        ? SHADOW_PATH.test(word)
        : isInRepo && /^agents(\.py|\/.*)?$/.test(word),
    )
}

// Guard 2: a rule edit can leave the generated catalogue stale.
const RULE_FILE = /(^|\/)agent_access\/rules\/[^/]+\.py$/
const SYNC = /agent_access\.catalogue\.sync\b/
const SYNC_REMINDER =
  'A rule file changed. If a @wcag_tool name, tech, uses or judgement changed, run `python -m agent_access.catalogue.sync` (CLAUDE.md).'

// Guard 3: runs that cost money or send page content to OpenAI.
const PAID_EVAL = /(-m\s+evals\.run_eval\b|evals\/run_eval\.py\b)/
const AUDIT_RUN = /(\bmain\.py\b|-m\s+agent_access\.cli\b)/
const TRACE_FLAG = /\s--trace(\s|=|$)/
const RUN = 'Run it'

/** Why a command needs the person's go-ahead, or undefined when it needs none. */
export const needsConfirmation = (command: string): string | undefined => {
  if (PAID_EVAL.test(command)) {
    return 'This runs the evals against a real model, which costs money.'
  }

  if (AUDIT_RUN.test(command) && TRACE_FLAG.test(command)) {
    return 'This audit runs with --trace, which sends page content and screenshots to OpenAI.'
  }

  return undefined
}

export const register: Register = on => {
  let isSyncDue = false

  on('tool.call', { tool: ['Write', 'Edit'] }, async ($, e, next) => {
    if (e.tool !== 'Write' && e.tool !== 'Edit') return next(e)

    if (SHADOW_PATH.test(e.file_path)) {
      return { deny: `${$.plugin.name}: ${SHADOW_DENY}` }
    }

    const ran = await next(e)

    if (ran.deny !== undefined || ran.isError === true) return ran
    if (!RULE_FILE.test(e.file_path) || isSyncDue) return ran

    isSyncDue = true
    $.ui.status('catalogue sync due')

    return { ...ran, context: [...(ran.context ?? []), SYNC_REMINDER] }
  }).catch(($, e, next) =>
    next.called ? next(e) : { deny: `${$.plugin.name}: its guard failed.` },
  )

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (shadowsSdk(e.command, await $.session.cwd())) {
      return { deny: `${$.plugin.name}: ${SHADOW_DENY}` }
    }

    const reason = needsConfirmation(e.command)

    if (reason !== undefined) {
      const answer = await $.ui
        .ask(`${reason} Run it?`, { options: [RUN, 'Cancel'], header: 'Confirm run' })
        .catch(() => 'nobody could be asked')

      if (answer !== RUN) {
        return {
          deny: `${$.plugin.name}: ${reason} The person did not approve it (${answer}).`,
        }
      }
    }

    const ran = await next(e)

    if (SYNC.test(e.command) && ran.deny === undefined && ran.isError !== true) {
      isSyncDue = false
      $.ui.status(undefined)
    }

    return ran
  }).catch(($, e, next) =>
    next.called ? next(e) : { deny: `${$.plugin.name}: its guard failed.` },
  )
}
