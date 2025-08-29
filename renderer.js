const e = React.createElement;

function App() {
  const TABS = {
    STREAM: 'stream',
    CONSOLE: 'console',
    SETTINGS: 'settings',
    ABOUT: 'about'
  };

  const [activeTab, setActiveTab] = React.useState(TABS.STREAM);

  // Settings state
  const [quality, setQuality] = React.useState('720p');
  const [bitrate, setBitrate] = React.useState('');
  const [bufferSize, setBufferSize] = React.useState('');
  const [overlayInfo, setOverlayInfo] = React.useState(false);
  const [shuffleOrder, setShuffleOrder] = React.useState(false);
  const [logToFile, setLogToFile] = React.useState(false);
  const [playlistUrl, setPlaylistUrl] = React.useState('');
  const [streamKey, setStreamKey] = React.useState('');
  const [savePlaylist, setSavePlaylist] = React.useState(false);
  const [saveStreamKey, setSaveStreamKey] = React.useState(false);

  function renderTabButton(tab, label) {
    return e('button', {
      className: activeTab === tab ? 'tab active' : 'tab',
      onClick: () => setActiveTab(tab)
    }, label);
  }

  function renderStreamTab() {
    return e('div', { className: 'tab-content' },
      e('button', { onClick: () => console.log('Start') }, 'Start'),
      e('button', { onClick: () => console.log('Stop') }, 'Stop'),
      e('button', { onClick: () => console.log('Skip Video') }, 'Skip Video')
    );
  }

  function renderConsoleTab() {
    return e('pre', { className: 'tab-content console' }, 'Console output...');
  }

  function renderSettingsTab() {
    return e('div', { className: 'tab-content settings' },
      e('div', null,
        e('label', null, 'Quality'),
        e('select', {
          value: quality,
          onChange: ev => setQuality(ev.target.value)
        }, [
          e('option', { value: '480p' }, '480p'),
          e('option', { value: '720p' }, '720p'),
          e('option', { value: '1080p' }, '1080p')
        ])
      ),
      e('div', null,
        e('label', null, 'Video Bitrate'),
        e('input', {
          value: bitrate,
          onChange: ev => setBitrate(ev.target.value)
        })
      ),
      e('div', null,
        e('label', null, 'Buffer Size'),
        e('input', {
          value: bufferSize,
          onChange: ev => setBufferSize(ev.target.value)
        })
      ),
      e('div', null,
        e('label', null,
          e('input', {
            type: 'checkbox',
            checked: overlayInfo,
            onChange: ev => setOverlayInfo(ev.target.checked)
          }),
          ' Overlay current VOD title and date'
        )
      ),
      e('div', null,
        e('label', null,
          e('input', {
            type: 'checkbox',
            checked: shuffleOrder,
            onChange: ev => setShuffleOrder(ev.target.checked)
          }),
          ' Shuffle order'
        )
      ),
      e('div', null,
        e('label', null,
          e('input', {
            type: 'checkbox',
            checked: logToFile,
            onChange: ev => setLogToFile(ev.target.checked)
          }),
          ' Log to file'
        )
      ),
      e('div', null,
        e('label', null, 'Playlist URL'),
        e('input', {
          value: playlistUrl,
          onChange: ev => setPlaylistUrl(ev.target.value)
        })
      ),
      e('div', null,
        e('label', null, 'Stream Key'),
        e('input', {
          value: streamKey,
          onChange: ev => setStreamKey(ev.target.value)
        })
      ),
      e('div', null,
        e('label', null,
          e('input', {
            type: 'checkbox',
            checked: savePlaylist,
            onChange: ev => setSavePlaylist(ev.target.checked)
          }),
          ' Save playlist'
        )
      ),
      e('div', null,
        e('label', null,
          e('input', {
            type: 'checkbox',
            checked: saveStreamKey,
            onChange: ev => setSaveStreamKey(ev.target.checked)
          }),
          ' Save stream key'
        )
      ),
      e('button', { onClick: () => console.log('Settings saved') }, 'Save Settings')
    );
  }

  function renderAboutTab() {
    return e('div', { className: 'tab-content about' }, 'Stream247 - simple streaming UI');
  }

  function renderActiveTab() {
    switch (activeTab) {
      case TABS.CONSOLE:
        return renderConsoleTab();
      case TABS.SETTINGS:
        return renderSettingsTab();
      case TABS.ABOUT:
        return renderAboutTab();
      case TABS.STREAM:
      default:
        return renderStreamTab();
    }
  }

  return e('div', { className: 'app' },
    e('h1', null, 'Stream247'),
    e('div', { className: 'tabs' },
      renderTabButton(TABS.STREAM, 'Stream'),
      renderTabButton(TABS.CONSOLE, 'Console'),
      renderTabButton(TABS.SETTINGS, 'Settings'),
      renderTabButton(TABS.ABOUT, 'About')
    ),
    renderActiveTab()
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(e(App));

