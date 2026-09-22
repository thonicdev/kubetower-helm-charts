// R2 - Conventional Commits, on commit messages and on pull-request titles.
// The config has to be in the tree: commitlint with no config refuses every
// subject it is given, valid ones included, with "Please add rules to your
// commitlint.config.js". A check that cannot pass is not a check.
module.exports = {
  extends: ['@commitlint/config-conventional'],

  rules: {
    // A scope enum, which `kubetower`'s config deliberately does not have.
    // The difference is the size of the set: that repository will have a scope
    // per Go package and a stale enum there would refuse legitimate commits.
    // Here there is one chart and a handful of surfaces, so the set is small
    // and closed, and R2 asks for the list to live beside the config rather
    // than in somebody's memory.
    'scope-enum': [2, 'always', [
      'chart',   // the chart's templates and values
      'rbac',    // the ClusterRole, its switches and the check over it
      'oidc',    // the Dex fixture under test/
      'ci',      // the workflows
      'docs',    // the README and the chart's NOTES
      'deps',    // versions this chart pins
    ]],
    'subject-case': [2, 'always', 'lower-case'],
  },
};
