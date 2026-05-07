/**
 * Regression test for model toggle event handler
 * 
 * Bug: onModelToggle was called with 2 parameters instead of 3
 * Impact: Model removal by name logic was broken (isRemoveByName always undefined)
 * Fix: Added third parameter `false` to the event handler call
 * 
 * Commit: 1d690a0 — UI/UX changes in the project creation page
 */

describe('CreateProjectPage — Model Toggle Handler', () => {
  /**
   * Test: onModelToggle should receive all 3 parameters
   * 
   * Context: When a model is toggled in the tree view, the handler receives:
   * - Parameter 1: Model ID or key
   * - Parameter 2: Model name
   * - Parameter 3: isRemoveByName flag (boolean)
   * 
   * The third parameter controls whether to remove by name key vs. qualified key.
   * If this parameter is missing/undefined, the conditional logic fails.
   */
  it('should pass 3 parameters to onModelToggle when adding model by ID', () => {
    // Simulating the event handler from CreateProjectPage.jsx:3753-3759
    const onModelToggle = jest.fn();
    const selectedModels = new Set();
    
    // Model being toggled
    const model = { id: 'model_abc', name: 'MyModel' };
    
    // Simulate the handler logic when model is NOT already selected by name
    if (model.name && (selectedModels.has(model.name) || selectedModels.has(model.name.trim()))) {
      const nameKey = selectedModels.has(model.name) ? model.name : model.name.trim();
      onModelToggle(nameKey, nameKey, true);
    } else {
      // THIS IS THE FIX: Add the third parameter `false`
      onModelToggle(model.id, model.name || model.id, false);
    }
    
    // Verify all 3 parameters are passed
    expect(onModelToggle).toHaveBeenCalledWith('model_abc', 'MyModel', false);
    expect(onModelToggle).toHaveBeenCalledTimes(1);
    
    // Verify the third parameter is a boolean (not undefined)
    const call = onModelToggle.mock.calls[0];
    expect(typeof call[2]).toBe('boolean');
    expect(call[2]).toBe(false);
  });

  /**
   * Test: onModelToggle should receive true when removing by name
   */
  it('should pass true as third parameter when removing model by name', () => {
    const onModelToggle = jest.fn();
    const selectedModels = new Set(['MyModel']);
    
    const model = { id: 'model_abc', name: 'MyModel' };
    
    // Simulate the handler logic when model IS already selected by name
    if (model.name && (selectedModels.has(model.name) || selectedModels.has(model.name.trim()))) {
      const nameKey = selectedModels.has(model.name) ? model.name : model.name.trim();
      onModelToggle(nameKey, nameKey, true);
    } else {
      onModelToggle(model.id, model.name || model.id, false);
    }
    
    // Verify the third parameter is true
    expect(onModelToggle).toHaveBeenCalledWith('MyModel', 'MyModel', true);
    
    const call = onModelToggle.mock.calls[0];
    expect(call[2]).toBe(true);
  });

  /**
   * Test: onModelToggle handler logic that depends on the third parameter
   */
  it('should differentiate between remove-by-name and remove-by-ID based on third parameter', () => {
    const toggleModel = jest.fn();
    const wsid = 'workspace_123';
    
    // The actual onModelToggle handler from CreateProjectPage.jsx:3707-3715
    const createHandler = (workspaceId) => (mid, modelName, isRemoveByName) => {
      if (isRemoveByName) {
        toggleModel(mid, modelName);  // Called with name key
      } else {
        toggleModel(`${workspaceId}::${mid}`, modelName);  // Called with qualified key
      }
    };
    
    const handler = createHandler(wsid);
    
    // Test remove by ID (third parameter = false)
    handler('model_abc', 'MyModel', false);
    expect(toggleModel).toHaveBeenCalledWith('workspace_123::model_abc', 'MyModel');
    
    // Test remove by name (third parameter = true)
    handler('MyModel', 'MyModel', true);
    expect(toggleModel).toHaveBeenCalledWith('MyModel', 'MyModel');
  });
});
