/**
 * useMoveArtifactToFolder: a refused move rolls back AND says so.
 *
 * The rollback is a refetch, so the card jumps back to its old folder. Without
 * a notice that reads as the move never having been asked for, which is the
 * silent-rollback gap useMoveSlotToFolder already closed for sessions.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'

const mocks = vi.hoisted(() => ({ setArtifactFolder: vi.fn() }))
vi.mock('../api/client', () => ({
  SEARCH_MIN_CHARS: 2,
  api: new Proxy(mocks as Record<string, unknown>, {
    get: (t, p: string) => (p in t ? t[p] : vi.fn().mockResolvedValue({})),
  }),
}))

import { store } from '../store'
import { setAgentSwitchNotice } from '../store/chatSlice'
import { useMoveArtifactToFolder } from '../hooks/useArtifactFolders'
import { ApiError } from '../api/apiError'
import { i18nT } from '../i18n/t'

function renderMove() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}><Provider store={store}>{children}</Provider></QueryClientProvider>
  )
  return { ...renderHook(() => useMoveArtifactToFolder(), { wrapper }), qc }
}

const settled = (qc: QueryClient) => qc.getMutationCache().getAll().map(m => m.state.status)

afterEach(() => {
  vi.clearAllMocks()
  store.dispatch(setAgentSwitchNotice(null))
})

describe('useMoveArtifactToFolder', () => {
  it('tells the user a refused move did not happen, with the gateway reason', async () => {
    mocks.setArtifactFolder.mockRejectedValueOnce(new ApiError(403, 'that folder belongs to another Bot'))
    const { result: move, qc } = renderMove()
    act(() => move.current('report', 'f2'))
    await waitFor(() => expect(settled(qc)).toEqual(['error']))
    expect(store.getState().chat.agentSwitchNotice).toEqual({
      title: i18nT('hooks.useArtifactFolders.move_refused'),
      message: 'that folder belongs to another Bot',
      error: true,
    })
  })

  it('says nothing when the move lands', async () => {
    mocks.setArtifactFolder.mockResolvedValueOnce({})
    const { result: move, qc } = renderMove()
    act(() => move.current('report', 'f2'))
    await waitFor(() => expect(settled(qc)).toEqual(['success']))
    expect(store.getState().chat.agentSwitchNotice).toBeNull()
  })

  it('reports nothing for a failed move a later move already superseded', async () => {
    let rejectFirst!: (e: Error) => void
    mocks.setArtifactFolder
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectFirst = reject }))
      .mockResolvedValueOnce({})
    const { result: move, qc } = renderMove()
    const invalidate = vi.spyOn(qc, 'invalidateQueries')
    act(() => move.current('report', 'f2'))
    act(() => move.current('report', 'f1'))
    await waitFor(() => expect(mocks.setArtifactFolder).toHaveBeenCalledWith('report', 'f1'))
    await act(async () => { rejectFirst(new Error('boom')) })
    await waitFor(() => expect(settled(qc)).toEqual(['error', 'success']))
    expect(store.getState().chat.agentSwitchNotice).toBeNull()
    // No rollback refetch either: read before the later move landed, it would
    // drop the card back into its old folder with nothing to correct it.
    const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey))
    expect(keys).not.toContain(JSON.stringify(['artifacts']))
  })
})
