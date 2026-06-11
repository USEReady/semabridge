import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\core\engine\deployment\databricks.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the body of _deploy_to_databricks to include routing
new_body = '''    def _deploy_to_databricks(self, context: RunContext) -> None:
        """Deploy SML metadata projection and measure views to Databricks.
        If skip_sml_conversion is True, deploy raw OSI views.
        """
        from semabridge.connectors.databricks_publisher import DatabricksPublisher
        
        options = getattr(context.config, "options", None)
        skip_sml = getattr(options, "skip_sml_conversion", False) if options else False
        
        if skip_sml:
            from semabridge.connectors.databricks_emitter import DatabricksEmitter
            logger.info("Deploying OSI directly to Databricks...")
            if not context.osi_model:
                raise DeploymentError("OSI model is missing but skip_sml_conversion is enabled.")
            emitter = DatabricksEmitter(context.config.databricks, behavior=context.behavior)
            deployed = emitter.deploy_from_osi(context.osi_model)
            if not deployed:
                raise DeploymentError(f"Databricks OSI deployment returned unsuccessful status: {emitter.last_deployment_error}")
            return'''

content = re.sub(
    r'    def _deploy_to_databricks\(self, context: RunContext\) -> None:.*?      from semabridge.connectors.databricks_publisher import DatabricksPublisher',
    new_body,
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched databricks.py deployment routing")
