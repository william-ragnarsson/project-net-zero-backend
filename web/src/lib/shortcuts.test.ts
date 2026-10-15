// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { isTypingTarget, layerOpen, shortcutFor } from './shortcuts'

const press = (key: string, target: EventTarget = document.body, init: KeyboardEventInit = {}) => {
  let seen: KeyboardEvent | null = null
  const grab = (e: Event) => {
    seen = e as KeyboardEvent
  }
  target.addEventListener('keydown', grab)
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init }))
  target.removeEventListener('keydown', grab)
  if (seen === null) throw new Error('no event')
  return seen
}

const mount = (html: string) => {
  document.body.innerHTML = html
  return document.body.firstElementChild as HTMLElement
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('shortcuts', () => {
  it('maps n and / outside text fields', () => {
    expect(shortcutFor(press('n'), false)).toBe('new')
    expect(shortcutFor(press('/'), false)).toBe('filter')
    expect(shortcutFor(press('x'), false)).toBeNull()
    expect(shortcutFor(press('N'), false)).toBeNull()
  })

  it('leaves keys alone while a modifier is held', () => {
    expect(shortcutFor(press('n', document.body, { metaKey: true }), false)).toBeNull()
    expect(shortcutFor(press('n', document.body, { ctrlKey: true }), false)).toBeNull()
    expect(shortcutFor(press('/', document.body, { altKey: true }), false)).toBeNull()
  })

  it('leaves keys alone while typing', () => {
    for (const html of [
      '<input>',
      '<textarea></textarea>',
      '<select></select>',
      '<div contenteditable="true"><p>text</p></div>',
      '<div role="slider" tabindex="0"></div>',
    ]) {
      const el = mount(html)
      const target = el.querySelector('p') ?? el
      expect(isTypingTarget(target)).toBe(true)
      expect(shortcutFor(press('n', target), false)).toBeNull()
    }
    expect(isTypingTarget(mount('<button>Go</button>'))).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })

  it('leaves keys alone while a dialog or menu is open', () => {
    expect(shortcutFor(press('n'), true)).toBeNull()

    mount('<div role="dialog" data-state="closed"></div>')
    expect(layerOpen()).toBe(false)
    mount('<div role="dialog" data-state="open"></div>')
    expect(layerOpen()).toBe(true)
    mount('<div role="menu" data-state="open"></div>')
    expect(layerOpen()).toBe(true)
  })
})
