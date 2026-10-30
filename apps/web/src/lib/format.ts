const dateTime = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Seoul",
});

export const formatDateTime = (iso: string) => dateTime.format(new Date(iso));

export const formatNumber = (n: number) => n.toLocaleString("en-US");

// Timed Posts (#26): the Admin picks times in KST (Korea has no daylight saving, so +09:00 is always right).
const kstParts = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** An ISO time → the `<input type="datetime-local">` value for the same moment in KST ("2026-10-31T19:00"). */
export const toKstInput = (iso: string | number) => kstParts.format(new Date(iso)).replace(" ", "T");

/** A datetime-local value read as KST → ISO with its offset, which is what the API wants. */
export const fromKstInput = (value: string) => `${value}:00+09:00`;

/** The datetime-local bounds for a Timed Post, fixed when the form opens: 5 minutes to 60 days from now, in KST. */
export const scheduleBounds = () => ({ min: toKstInput(Date.now() + 5 * 60_000), max: toKstInput(Date.now() + 60 * 86_400_000) });
