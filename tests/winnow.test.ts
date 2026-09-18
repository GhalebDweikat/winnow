import { describe, expect, test } from 'claude-code/testing'
import type { PromptSubmitInput } from 'claude-code'
import { describeMeta, head, tail, taskFrom } from '../hooks/winnow'

describe('taskFrom', () => {
  test('the last human request, and what the assistant said since', () => {
    const task = taskFrom([
      { role: 'user', text: 'Fix the flaky test in test_serve.py', toolUses: [] },
      {
        role: 'assistant',
        text: 'I will read the test first.',
        toolUses: [{ tool_use_id: 't1', tool: 'Read', input: { file_path: 'x' } }],
      },
      { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: 't1', text: 'def test...', isError: false }] },
      { role: 'assistant', text: 'The retry loop is the problem. Now the server.', toolUses: [] },
    ])
    expect(task.user_request).toBe('Fix the flaky test in test_serve.py')
    expect(task.assistant_intent).toBe('The retry loop is the problem. Now the server.')
  })

  test('a new human turn resets the assistant intent', () => {
    const task = taskFrom([
      { role: 'user', text: 'first ask', toolUses: [] },
      { role: 'assistant', text: 'working on the first ask', toolUses: [] },
      { role: 'user', text: 'second ask', toolUses: [] },
    ])
    expect(task).toEqual({ user_request: 'second ask', assistant_intent: '' })
  })

  test('nothing said is an empty task', () => {
    expect(taskFrom([])).toEqual({ user_request: '', assistant_intent: '' })
  })

  test('long text keeps the head of a request and the tail of an intent', () => {
    expect(head('abcdef', 4)).toBe('abc…')
    expect(tail('abcdef', 4)).toBe('…def')
    expect(head('abc', 4)).toBe('abc')
  })
})

describe('describeMeta', () => {
  test('names the tool, the counts and the recall key', () => {
    const text = describeMeta('Read', { hidden: 3, blocks: 8, before: 5120, after: 1800, key: 'ab12' })
    expect(text).toBe('winnow: hid 3 of 8 blocks of Read (5.1k to 1.8k chars; winnow_recall ab12)')
  })
})

describe('tool.call', () => {
  test('a large result passes through untouched when the sidecar is not reachable', async ($, on) => {
    const big = { stdout: 'ok\n'.repeat(2000), stderr: '', interrupted: false, isImage: false }
    on('tool.call', { tool: 'Bash' }, () => ({ result: big }))
    const answer = await $.tool.call({ tool: 'Bash', command: 'true' })
    expect(answer.result).toEqual(big)
  })

  test('a small result is returned as is', async ($, on) => {
    const small = { type: 'text', file: { filePath: 'a.py', content: 'x = 1\n', numLines: 1, startLine: 1, totalLines: 1 } }
    on('tool.call', { tool: 'Read' }, () => ({ result: small }))
    const answer = await $.tool.call({ tool: 'Read', file_path: 'a.py' })
    expect(answer.result).toEqual(small)
  })

  test('a denied call is left alone', async ($, on) => {
    on('tool.call', { tool: 'Grep' }, () => ({ deny: 'not in tests' }))
    const answer = await $.tool.call({ tool: 'Grep', pattern: 'x' })
    expect(answer).toMatchObject({ deny: 'not in tests' })
  })
})

describe('prompt.submit', () => {
  test('the prompt goes through unchanged when the sidecar is not reachable', async ($, on) => {
    on('prompt.submit', (_$, e) => ({ text: e.text, context: e.context }))
    // The engine fills wait and origin for a plugin's own submission; the declared type asks for them anyway.
    const out = await $.prompt.submit({ text: 'What does the sidecar do when it is down?' } as PromptSubmitInput)
    expect(out).toMatchObject({ text: 'What does the sidecar do when it is down?' })
  })
})
