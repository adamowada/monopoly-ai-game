import { ShieldAlert } from "lucide-react";

import type { RejectedActionRecord } from "../lib/api/rejected-actions";
import { cn } from "../lib/ui";

type RejectedActionAuditViewProps = {
  records: RejectedActionRecord[];
};

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}

function ReasonBadge({ reasonCode }: Readonly<{ reasonCode: string }>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200",
      )}
    >
      <svg viewBox="0 0 6 6" aria-hidden="true" className="size-1.5 fill-rose-500">
        <circle r={3} cx={3} cy={3} />
      </svg>
      {reasonCode}
    </span>
  );
}

function validationSummary(record: RejectedActionRecord): string {
  if (record.validation_errors.length === 0) {
    return "No validation details supplied.";
  }
  return record.validation_errors
    .map((error) => {
      const field = error.field ? `${error.field}: ` : "";
      return `${field}${error.message}`;
    })
    .join(" ");
}

export function RejectedActionAuditView({ records }: RejectedActionAuditViewProps) {
  return (
    <div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="rejected-actions-title" className="text-base font-semibold text-neutral-950">
            Rule rulings
          </h2>
          <p className="mt-2 text-sm text-neutral-600">
            Rejected moves stay separate from accepted table events.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-neutral-600">
          <ShieldAlert aria-hidden="true" className="size-4 text-rose-600" />
          <span>{records.length} records</span>
        </div>
      </div>

      {records.length === 0 ? (
        <div className="mt-5 border-y border-neutral-200 bg-white px-4 py-6 text-sm text-neutral-600 sm:px-6">
          No rejected actions recorded.
        </div>
      ) : (
        <div className="mt-5 overflow-hidden border-y border-neutral-200 bg-white">
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
                <tr>
                  <th scope="col" className="px-4 py-3 font-semibold sm:px-6">
                    Actor
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Reason
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Phase
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Action
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Timestamp
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Validation details
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200">
                {records.map((record) => (
                  <tr key={record.id}>
                    <th
                      scope="row"
                      className="max-w-xs break-all px-4 py-4 font-medium text-neutral-950 sm:px-6"
                    >
                      {record.actor_player_id ?? "Unknown actor"}
                    </th>
                    <td className="whitespace-nowrap px-4 py-4">
                      <ReasonBadge reasonCode={record.reason_code} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-4 text-neutral-700">
                      {record.phase ?? "Unknown phase"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-4 font-medium text-neutral-800">
                      {record.action_type}
                    </td>
                    <td className="whitespace-nowrap px-4 py-4 text-neutral-700">
                      {formatTimestamp(record.created_at)}
                    </td>
                    <td className="min-w-80 max-w-xl px-4 py-4 text-neutral-700">
                      {validationSummary(record)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
