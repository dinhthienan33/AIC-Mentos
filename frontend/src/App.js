import React, { useState } from 'react';
import SearchInterface from './components/SearchInterface';
import VideoPlayModal from './components/VideoPlayModal';
import SiteFooter from './components/SiteFooter';
import LoginPage from './components/LoginPage';
import DresDock from './components/DresDock';
import { DresProvider } from './dres/DresContext';
import { isAuthenticated, login, logout } from './auth';
import './App.css';

function App() {
  const [authed, setAuthed] = useState(isAuthenticated());
  const [videoPlayer, setVideoPlayer] = useState(null);

  const openVideoPlayer = (keyframe) => {
    const startTime = keyframe.timestamp || 0;

    setVideoPlayer({
      videoUrl: keyframe.video_url,
      startSeconds: startTime,
      videoId: keyframe.video_id,
      frameRate: keyframe.fps ?? keyframe.frame_rate,
      keyframeRefs: [{
        timestamp: startTime,
        keyframe_num: keyframe.keyframe_num,
        confidence_score: keyframe.confidence_score,
      }],
      markers: [startTime],
      csvBaseName: keyframe.video_id,
    });
  };

  const closeVideoPlayer = () => {
    setVideoPlayer(null);
  };

  const handleLogin = (username, password) => {
    const ok = login(username, password);
    if (ok) {
      setAuthed(true);
    }
    return ok;
  };

  const handleLogout = () => {
    logout();
    setAuthed(false);
    setVideoPlayer(null);
  };

  if (!authed) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <DresProvider>
    <div className="App">
      <div className="app-topbar">
        <span className="app-topbar-title">Visual Search</span>
        <button type="button" className="app-logout-btn" onClick={handleLogout}>
          Logout
        </button>
      </div>
      <DresDock />
      <SearchInterface onOpenVideo={openVideoPlayer} />
      <SiteFooter />
      {videoPlayer && (
        <VideoPlayModal
          videoUrl={videoPlayer.videoUrl}
          startSeconds={videoPlayer.startSeconds}
          videoId={videoPlayer.videoId}
          keyframeRefs={videoPlayer.keyframeRefs}
          markers={videoPlayer.markers}
          csvBaseName={videoPlayer.csvBaseName}
          frameRate={videoPlayer.frameRate}
          onClose={closeVideoPlayer}
        />
      )}
    </div>
    </DresProvider>
  );
}

export default App;
