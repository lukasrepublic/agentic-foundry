# ux kit

UI blueprints only. Rules FG-UX-001..003: WCAG target with axe in e2e, a front-end performance budget as a report,
no literal strings.

`applies_to.blueprints: ["ui"]` is a SURFACE tag. It is consumed when the resolver gains surface filtering (gv-15);
until then `render` still emits the ux category.

Automated accessibility checks cover a minority of WCAG success criteria (roughly a third); the rest
(manual and assistive-technology review) is a practice, not a control.

The Lighthouse budget reports and does not gate.
