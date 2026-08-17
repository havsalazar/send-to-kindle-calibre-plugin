from calibre.customize import InterfaceActionBase

class SendToKindlePlugin(InterfaceActionBase):
    name                = 'Send to Kindle'
    description         = 'Send the selected books to Kindle via StkSendToHandler.exe'
    supported_platforms = ['windows']
    author              = 'havsalazar'
    version             = (1, 1, 1)
    minimum_calibre_version = (5, 0, 0)

    actual_plugin = 'calibre_plugins.send_to_kindle.action:SendToKindleAction'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.send_to_kindle.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
