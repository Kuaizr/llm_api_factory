import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#0b0b0f",
        foreground: "#f4f4f5",
        panel: "#14141b",
        muted: "#27272a"
      }
    }
  },
  plugins: []
} satisfies Config;
