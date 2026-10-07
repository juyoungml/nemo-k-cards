import type { Metadata, Viewport } from "next";
import { Geist_Mono } from "next/font/google";
import localFont from "next/font/local";

import { AccessGate } from "@/components/access-gate";
import { MobileNav, Sidebar } from "@/components/sidebar";
import "./globals.css";

// Brand type (DESIGN.md): Pretendard for UI and Korean, Cabinet Grotesk 800 for page titles.
const pretendard = localFont({
  variable: "--font-pretendard",
  src: [
    { path: "./fonts/Pretendard-Regular.woff2", weight: "400" },
    { path: "./fonts/Pretendard-Bold.woff2", weight: "700" },
    { path: "./fonts/Pretendard-Black.woff2", weight: "900" },
  ],
});

const cabinet = localFont({
  variable: "--font-cabinet",
  src: [{ path: "./fonts/CabinetGrotesk-Extrabold.woff2", weight: "800" }],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "What's On Korea · Admin",
  description: "Research, verify and publish Korean event card news for foreigners.",
};

// Phones: fit the notch / home bar (the mobile header and tab bar pad with safe-area insets).
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${pretendard.variable} ${cabinet.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-canvas md:flex-row">
        <Sidebar />
        <MobileNav />
        <main className="min-w-0 flex-1 space-y-5 px-4 pt-5 pb-28 md:space-y-6 md:px-10 md:py-8">{children}</main>
        <AccessGate />
      </body>
    </html>
  );
}
