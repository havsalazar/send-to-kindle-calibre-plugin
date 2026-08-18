from calibre.customize import InterfaceActionBase

class SendToKindlePlugin(InterfaceActionBase):
    name                = 'Send to Kindle'
    description         = ("Send the selected books to your Kindle devices over Amazon's "
                           'Send to Kindle service, without the desktop application')
    supported_platforms = ['windows', 'osx', 'linux']
    author              = 'havsalazar'
    version             = (2, 0, 0)
    # qt.core, which every module here imports, only exists from calibre 6
    minimum_calibre_version = (6, 0, 0)

    actual_plugin = 'calibre_plugins.send_to_kindle.action:SendToKindleAction'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.send_to_kindle.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
