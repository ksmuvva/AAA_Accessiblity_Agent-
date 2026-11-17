import { expect, test } from 'claude-code/testing'

const REPO = '/home/user/AAA_Accessiblity_Agent-'

test('a top-level agents module is refused, a nested one is not', async ($, on) => {
  const written: string[] = []
  on('tool.call', (_, e) => {
    if (e.tool === 'Write') written.push(e.file_path)

    return { result: {} }
  })

  const top = await $.tool.call({ tool: 'Write', file_path: `${REPO}/agents.py`, content: '' })
  const pkg = await $.tool.call({ tool: 'Write', file_path: `${REPO}/agents/__init__.py`, content: '' })
  await $.tool.call({ tool: 'Write', file_path: `${REPO}/agent_access/agents.py`, content: '' })

  expect(top.deny).toMatch(/shadows the OpenAI Agents SDK/)
  expect(pkg.deny).toMatch(/shadows the OpenAI Agents SDK/)
  expect(written).toEqual([`${REPO}/agent_access/agents.py`])
})

test('a shell command that creates agents is refused only in the repo', async ($, on) => {
  let cwd = REPO
  const ran: string[] = []
  on('session.cwd', () => ({ value: cwd }))
  on('tool.call', (_, e) => {
    if (e.tool === 'Bash') ran.push(e.command)

    return { result: {} }
  })

  expect((await $.tool.call({ tool: 'Bash', command: 'mkdir agents' })).deny).toBeDefined()
  expect((await $.tool.call({ tool: 'Bash', command: 'touch ./agents.py' })).deny).toBeDefined()
  await $.tool.call({ tool: 'Bash', command: 'ls agents' })
  await $.tool.call({ tool: 'Bash', command: 'mkdir agent_access/agents' })
  cwd = '/home/user/other'
  await $.tool.call({ tool: 'Bash', command: 'mkdir agents' })
  expect((await $.tool.call({ tool: 'Bash', command: `mkdir ${REPO}/agents` })).deny).toBeDefined()

  expect(ran).toEqual(['ls agents', 'mkdir agent_access/agents', 'mkdir agents'])
})

test('a paid or traced run waits for the person', async ($, on) => {
  let answer = 'Cancel'
  const asked: string[] = []
  const ran: string[] = []
  on('session.cwd', () => ({ value: REPO }))
  on('tool.call', (_, e) => {
    if (e.tool === 'AskUserQuestion') {
      const question = e.questions[0]?.question ?? ''
      asked.push(question)

      return { result: { questions: e.questions, answers: { [question]: answer } } }
    }
    if (e.tool === 'Bash') ran.push(e.command)

    return { result: {} }
  })

  const evals = await $.tool.call({ tool: 'Bash', command: 'python -m evals.run_eval --cases a' })
  const traced = await $.tool.call({ tool: 'Bash', command: 'python main.py https://x.test --trace' })
  expect(evals.deny).toMatch(/did not approve/)
  expect(traced.deny).toMatch(/did not approve/)

  answer = 'Run it'
  await $.tool.call({ tool: 'Bash', command: 'python -m evals.run_eval' })
  await $.tool.call({ tool: 'Bash', command: 'python main.py https://x.test --no-trace' })
  await $.tool.call({ tool: 'Bash', command: 'python -m evals.act_eval ../act-rules' })

  expect(asked.length).toBe(3)
  expect(ran).toEqual([
    'python -m evals.run_eval',
    'python main.py https://x.test --no-trace',
    'python -m evals.act_eval ../act-rules',
  ])
})

test('a rule edit reminds once, and the sync clears it', async ($, on) => {
  const statuses: (string | undefined)[] = []
  on('session.cwd', () => ({ value: REPO }))
  on('ui.status', (_, e) => {
    statuses.push(e.text)

    return { value: undefined }
  })
  on('tool.call', () => ({ result: {} }))

  const rule = { tool: 'Edit', file_path: `${REPO}/agent_access/rules/operable.py`, old_string: 'a', new_string: 'b' } as const
  const first = await $.tool.call(rule)
  const second = await $.tool.call(rule)
  const other = await $.tool.call({ ...rule, file_path: `${REPO}/agent_access/engine.py` })
  await $.tool.call({ tool: 'Bash', command: 'python -m agent_access.catalogue.sync' })

  expect(first.context?.join(' ')).toMatch(/catalogue\.sync/)
  expect(second.context).toBeUndefined()
  expect(other.context).toBeUndefined()
  expect(statuses).toEqual(['catalogue sync due', undefined])
})
