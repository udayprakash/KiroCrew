import { useState, useEffect } from 'react'
import { CheckCircle, Trash2 } from 'lucide-react'
import { useAppDispatch } from '../../store'
import { ackNotification, deleteNotification } from '../../store/notificationsSlice'
import { api } from '../../api/client'
import ErrorNotice from '../../components/ErrorNotice'
import type { Notification } from '../../types'

import { i18nT } from '../../i18n/t'

/** Why an acknowledgement did not save: the job is gone, or the request failed. */
type AckFailure = 'gone' | 'retry'

export default function CronAckBar({ notification, onDone }: { notification: Notification; onDone: () => void }) {
  const [acked, setAcked] = useState(!!notification.acked)
  const [failure, setFailure] = useState<AckFailure | null>(null)
  const dispatch = useAppDispatch()
  useEffect(() => { setAcked(!!notification.acked) }, [notification.acked])
  const deleteButton = (
    <button className="px-3 py-1.5 rounded-md text-[13px] text-danger hover:text-danger-text border border-border hover:border-danger/50 hover:bg-danger/10 transition-all cursor-pointer bg-transparent" onClick={() => { dispatch(deleteNotification(notification.ts)); onDone() }}><Trash2 className="lucide-inline" /> {i18nT('pages.chat.cronAckBar.delete')}</button>
  )
  return (
    <div className="mt-4 pt-3 border-t border-border">
      {acked ? (
        <div className="flex items-center gap-3">
          <div className="flex-1 px-3.5 py-2.5 rounded-lg bg-accent-subtle border border-accent/20 text-sm text-text"><CheckCircle className="lucide-inline" /> {i18nT('pages.chat.cronAckBar.acknowledged_this_won_t_be_repeated_in_future_no')}</div>
          {deleteButton}
        </div>
      ) : (
        <div className="flex flex-col items-start gap-2">
          <div className="flex items-center gap-3">
            {failure !== 'gone' && (
              <button className="px-4 py-2 rounded-lg bg-accent text-accent-fg text-[13px] font-semibold cursor-pointer border-none hover:brightness-110 transition-all" onClick={async () => {
                const jobId = notification.job_id as string
                setFailure(null)
                // Only a saved acknowledgement reads as "Acknowledged": a refusal
                // ({ ok: false }) or a failed request keeps the note unread.
                let next: AckFailure | null
                try {
                  const res = await api.ackCron(jobId, (notification.body || '').slice(0, 200), notification.ts)
                  next = res?.ok === true ? null : res?.code === 'job_not_found' ? 'gone' : 'retry'
                } catch {
                  next = 'retry'
                }
                if (next) {
                  setFailure(next)
                  return
                }
                dispatch(ackNotification(notification.ts))
                setAcked(true)
              }}><CheckCircle className="lucide-inline" /> {i18nT('pages.chat.cronAckBar.acknowledge')}</button>
            )}
            {/* A note whose job is gone can never be acknowledged; Delete is its way out. */}
            {failure === 'gone' && deleteButton}
          </div>
          {/* No hand-off: the notification panel can sit over a page with an unsaved draft. */}
          <ErrorNotice
            variant="inline"
            message={failure === 'gone'
              ? i18nT('pages.chat.cronAckBar.ack_job_gone')
              : failure === 'retry' ? i18nT('pages.chat.cronAckBar.ack_failed') : null}
          />
        </div>
      )}
    </div>
  )
}
