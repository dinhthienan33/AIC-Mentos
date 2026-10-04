import React, { useState } from 'react';
import SearchInterface from './components/SearchInterface';
import VideoPlayModal from './components/VideoPlayModal';
import SiteFooter from './components/SiteFooter';
import DresDock from './components/DresDock';
import { DresProvider } from './dres/DresContext';
import './App.css';

function App() {
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

  return (
    <DresProvider>
    <div className="App">
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
