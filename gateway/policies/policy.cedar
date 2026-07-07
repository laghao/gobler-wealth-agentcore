// Cedar policy for the Gobler AgentCore Gateway tool access control.
//
// HOW IT WORKS:
// The Gateway's JWT Authorizer maps M2M JWT claims to Cedar principal tags:
//   JWT claim "department" -> principal.getTag("department")
//   JWT claim "role"       -> principal.getTag("role")
//   JWT claim "user_id"    -> principal.getTag("user_id")
//
// These are CUSTOM claims injected by the Cognito V3 Pre-Token Lambda via
// claimsToAddOrOverride. Standard claims (sub, iss, client_id, scope, exp) are
// also available as principal tags and managed automatically by Cognito.
//
// IMPORTANT — SINGLE STATEMENT ONLY:
// The AgentCore create_policy API accepts ONE Cedar statement per policy
// (definition={"cedar": {"statement": <document>}}). The CDK loader strips
// "//" comment lines and joins the rest into that single statement. Therefore
// you must NOT put multiple `permit(...)` blocks here — use a single permit
// with `action in [ ... ]` to authorize several tools at once. Multiple permit
// statements produce: "unexpected token `permit`".
//
// ACTION NAME FORMAT: "<TargetName>___<tool_name>" (triple underscore).
//   TargetName comes from gatewayTargetName in backend-construct.ts.
//   tool_name comes from the "name" field in each tool_spec.json.
//
// {{GATEWAY_ARN}} is replaced by CDK at deploy time with the actual Gateway ARN.
//
// TO CHANGE ACCESS: edit this file, then run `cdk deploy` from infra-cdk/.
//
// ========================================
// Gobler tool access (all three tools, one statement)
// ========================================
// Permitted for the finance department and guest (demo) principals. Add or
// remove departments in the `when` block, or add/remove tool actions in the
// `action in [ ... ]` list, then redeploy. Cedar is deny-by-default.

permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"exposure___look_through_exposure",
    AgentCore::Action::"rebalance___tax_rebalancing",
    AgentCore::Action::"deploy___opportunistic_deployment"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "guest")
};
