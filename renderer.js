const e = React.createElement;

function App() {
  const [playlistUrl, setPlaylistUrl] = React.useState('');
  const [streamKey, setStreamKey] = React.useState('');

  return e('div', { className: 'app' },
    e('h1', null, 'Stream247'),
    e('div', null,
      e('label', null, 'Playlist URL:'),
      e('input', {
        value: playlistUrl,
        onChange: ev => setPlaylistUrl(ev.target.value)
      })
    ),
    e('div', null,
      e('label', null, 'Stream Key:'),
      e('input', {
        value: streamKey,
        onChange: ev => setStreamKey(ev.target.value)
      })
    ),
    e('button', {
      onClick: () => {
        console.log('Start streaming not implemented');
      }
    }, 'Start Stream')
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(e(App));
