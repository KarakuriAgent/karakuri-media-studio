import type { CanvasTextData } from '../../../types'
import { AreaField } from './common'

export default function TextFields({
  data,
  onChange,
}: {
  data: CanvasTextData
  onChange: (data: CanvasTextData) => void
}) {
  return (
    <AreaField
      label="本文"
      value={data.body}
      rows={10}
      onChange={(body) => onChange({ ...data, body })}
    />
  )
}
