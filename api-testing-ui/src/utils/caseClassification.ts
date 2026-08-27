export function hasExplicitOneTimeMarker(values: Array<string | null | undefined>): boolean {
  const text = values.filter(Boolean).join(' ').toLocaleLowerCase()
  return text.includes('一次性') || /\bone[- ]time\b/.test(text)
}
