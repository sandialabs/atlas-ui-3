The expected behavior is
1. FORCE_TOOL_APPROVAL_GLOBALLY=false requires all tools to be approves. overrides anything else. 
2. REQUIRE_TOOL_APPROVAL_BY_DEFAULT=true make it so if there is an option to auto approve, then approval is needed by default, but the user can turn it off. 
All tools when seeking approval in the UI can have their args edited. no need for a special case of allowing edits for some and not others. 
3. in the mcp.json default and override, a key:value for require_approval is the key. The admin can set somem functions to always requires approval (for example dangerious commands). The usr cannot turn this off. 
4. The usre can easily toggle auto approval via the UI settings panel OR when a tool approval is requested an an inline ui element that is adjancent to the approval requests. the use can always enable tool approval for themselves so it is not auto approved by toggleing the global toggle. 
5. the UI should not say "thinking" while the UI is waiting on an approval. 

can you check the code and verify these requirments are met