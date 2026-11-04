/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,
  // lets `NEXT_DIST_DIR=.next-build next build` run without disturbing a running dev server
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
