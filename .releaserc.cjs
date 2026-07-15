const parserOpts = {
  noteKeywords: ["BREAKING CHANGE", "BREAKING CHANGES"],
  // Only treat an explicit BREAKING CHANGE note as major.
  breakingHeaderPattern: /^$/,
};

module.exports = {
  branches: ["master"],
  tagFormat: "${version}",
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "angular",
        releaseRules: [
          { breaking: true, release: "major" },
          { type: "feat", release: "minor" },
          { type: "fix", release: "patch" },
          { type: "perf", release: "patch" },
          { type: "docs", release: false },
          { type: "test", release: false },
          { type: "refactor", release: false },
          { type: "chore", release: false },
          { type: "build", release: false },
          { type: "ci", release: false },
          { type: "style", release: false },
        ],
        parserOpts,
      },
    ],
    [
      "@semantic-release/release-notes-generator",
      {
        preset: "angular",
        parserOpts,
      },
    ],
    "@semantic-release/changelog",
    "@semantic-release/git",
    "@semantic-release/github",
  ],
};
