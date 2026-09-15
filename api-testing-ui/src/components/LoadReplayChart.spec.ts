// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadReplayChart from './LoadReplayChart.vue'
const props = { title: '压力', unit: '次/秒', start: 100, end: 120, cursor: 100, windows: [100,105,110,115], lines: [
  { id: 'plan', name: '计划', color: 'blue', unit: '次/秒', points: [{time:100,value:4},{time:120,value:4}] },
  { id: 'actual', name: '实际', color: 'green', unit: '次/秒', points: [{time:100,value:2},{time:105,value:4},{time:115,value:1}] },
] }
describe('LoadReplayChart', () => {
  it('shows units, elapsed ticks and a vertical cursor on a shared scale', () => {
    const w = mount(LoadReplayChart, {props})
    expect(w.text()).toContain('次/秒')
    expect(w.text()).toContain('00:20')
    const cursor = w.get('[data-testid="time-cursor"]')
    expect(cursor.attributes('x1')).toBe(cursor.attributes('x2'))
    const lines = w.findAll('polyline')
    expect(lines[0].attributes('points')!.split(' ')[0].split(',')[1]).toBe(lines[1].attributes('points')!.split(' ')[1].split(',')[1])
    expect(lines.length).toBe(3) // Actual series breaks at missing bucket.
  })
  it('provides visible selection details and keyboard navigation', async () => {
    const w = mount(LoadReplayChart, {props})
    await w.get('[data-time="105"]').trigger('click')
    expect(w.emitted('seek')?.[0]).toEqual([105])
    await w.get('[data-time="105"]').trigger('mouseenter')
    expect(w.get('[role="status"]').text()).toContain('00:05')
    expect(w.get('[role="status"]').text()).toContain('4 次/秒')
    await w.get('svg').trigger('keydown', {key:'ArrowRight'})
    expect(w.emitted('seek')?.at(-1)).toEqual([105])
  })
  it('draws the threshold horizontally and does not carry stale samples forward', () => {
    const w = mount(LoadReplayChart, {props:{...props,cursor:110,reference:6}})
    const line = w.get('[data-testid="threshold"]')
    expect(line.attributes('y1')).toBe(line.attributes('y2'))
    expect(w.get('[role="status"]').text()).toContain('无采样')
    expect(w.text()).toContain('验收上限 6 次/秒')
  })
})
