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
