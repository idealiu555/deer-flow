/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import "./src/env.js";

/** @type {import("next").NextConfig} */
const config = {
  output: "standalone", // Enable standalone output for production Docker deployment
  devIndicators: false,
  allowedDevOrigins: ["127.0.0.1", "localhost", "::1"],
};

export default config;
