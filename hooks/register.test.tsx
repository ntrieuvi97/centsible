import { expect, test } from 'claude-code/testing'

test('the pane draws before any data is read', async ($, on) => {
  on('ui.open', () => ({ value: { isPlaced: true } }))

  const ui = await $.ui.mount({
    plugin: 'centsible',
    component: 'Pane',
    requestId: 'centsible',
    surface: 'terminal',
    props: { title: 'Centsible', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 20 }, view: {} },
  })

  expect((await ui.find({ type: 'Text', text: /No events yet/ }))?.text).toBe('No events yet.')
  await ui.unmount()
})
