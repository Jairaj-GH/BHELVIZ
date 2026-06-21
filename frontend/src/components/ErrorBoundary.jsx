import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props){ super(props); this.state = { hasError: false }; }
  static getDerivedStateFromError(){ return { hasError: true }; }
  componentDidCatch(err, info){ console.error('UI Error:', err, info); }
  render(){
    if(this.state.hasError){
      return (<div style={{padding:24}}><h3>Something went wrong</h3><p>Try reloading the view. If problem persists, contact support.</p></div>);
    }
    return this.props.children;
  }
}
