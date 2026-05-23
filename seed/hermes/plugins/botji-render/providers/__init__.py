"""V1R render providers — concrete implementations of the operations interface.

Each provider exposes the same call shape (``generate_image(sources, policy,
output_dir, **kwargs) -> RenderResult``) so the operations dispatcher can
route to whichever provider the workflow / config selects.
"""
